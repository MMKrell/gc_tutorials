#!/usr/bin/env python
# Copyright (c) 2019 Graphcore Ltd. All rights reserved.

'''
Benchmarks a TensorFlow graph.

Usage: benchmark.py <benchmark_import_name> [-h --options]
'''
import tensorflow.compat.v1 as tf
import copy
import time
import os
import json
import argparse
from collections import namedtuple

from tensorflow.python.ipu.config import IPUConfig, DeviceConnectionType
from tensorflow.python.ipu.scopes import ipu_scope
from tensorflow.python.ipu import utils, loops, ipu_infeed_queue, ipu_compiler

Benchmark = namedtuple(
    'Benchmark', [
        'graph_builder',     # opts, inputs -> [fetches]
        'inputs',            # opts, index -> inputs. This maps index -> tensors to be consumed by graph_builder
        'initializer',       # -> [fetches]
        'add_args',          # parser -> parser
        'iteration_report',  # opts, duration -> string
        'initializer_sess',  # sess -> None. Run an initialiser with direct access to the session.
        'out_shape'          # graph_builder, opts, inputs -> np.array. Return array wirth correct output dtype and shape
                             # so that the benchmark does not have to deduce the size by constructing
                             # a graph.
    ]
)
Benchmark.__new__.__defaults__ = (None,) * len(Benchmark._fields)


def run(benchmark, opts):
    '''
    Run the benchmark.

    benchmark - An instance of Benchmark
    opts - Namespace from argparse generated from parse_opts
    '''
    # Build graph
    with tf.device('cpu'):
        dataset = tf.data.Dataset \
            .range((opts.steps + 2) * opts.batches_per_step) \
            .map(lambda i: benchmark.inputs(opts, i)) \
            .repeat() \
            .prefetch(opts.batches_per_step)

        if opts.batches_per_step > 1 or opts.replicas > 1:
            infeed_queue = ipu_infeed_queue.IPUInfeedQueue(dataset)
            data_init = infeed_queue.initializer
        else:
            data_tensor = dataset.make_one_shot_iterator().get_next()
            data_init = tf.no_op()

    with ipu_scope('/device:IPU:0'):
        if opts.batches_per_step > 1:
            with tf.Graph().as_default():  # To get the shape and dtype
                dummy_opts = copy.deepcopy(opts)
                dummy_opts.shards = 1
                d = benchmark.inputs(dummy_opts, tf.constant(0))
                if benchmark.out_shape is None:
                    out = benchmark.graph_builder(dummy_opts, d)
                else:
                    out = benchmark.out_shape(benchmark.graph_builder, dummy_opts, d)
            input = tf.constant(0, out.dtype, shape=out.shape)

            def body(inout, *args, **kwargs):
                with tf.control_dependencies([inout]):
                    # Run graph
                    out = benchmark.graph_builder(opts, kwargs)
                return out

            out = ipu_compiler.compile(lambda: loops.repeat(opts.batches_per_step,
                                                            body,
                                                            [input],
                                                            infeed_queue), [])
        else:
            opts.batches_per_step = 1
            if opts.replicas > 1:
                out = ipu_compiler.compile(lambda: benchmark.graph_builder(opts, infeed_queue), [])
            else:
                out = ipu_compiler.compile(lambda: benchmark.graph_builder(opts, data_tensor), [])

    # Dump the graph to a logdir
    if opts.save_graph:
        writer = tf.summary.FileWriter(os.path.join(os.path.dirname(os.path.realpath(__file__)), 'logs', time.strftime('%Y%m%d_%H%M%S_%Z')))
        writer.add_graph(tf.get_default_graph())

    utils.configure_ipu_system(get_config(opts))
    utils.move_variable_initialization_to_cpu()

    with tf.Session() as sess:
        # Setup
        sess.run(data_init)
        if benchmark.initializer is not None:
            sess.run(benchmark.initializer())
        if benchmark.initializer_sess is not None:
            benchmark.initializer_sess(sess)

        # Warmup
        print("Compiling and Warmup...")
        start = time.time()
        sess.run(out)
        duration = time.time() - start
        print("Duration: {:.3f} seconds\n".format(duration))

        print("Executing...")
        average_batches_per_sec = 0
        # steps
        for i in range(opts.steps):
            # Run
            start = time.time()
            sess.run(out)
            duration = time.time() - start

            average_batches_per_sec += (opts.batches_per_step*opts.replicas/duration)/opts.steps
            report_string = "{:<7.3} sec/itr.".format(duration)
            report_string += "   " + benchmark.iteration_report(opts, duration)
            print(report_string)

        return average_batches_per_sec

# --------- UTILS --------------


def parse_opts(benchmark, arg_string=None):
    parser = argparse.ArgumentParser(description='Synthetic Benchmarks in TensorFlow', formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    # Default Arguments
    parser.add_argument('--use-generated-data', action="store_true",
                        help="Add data transfer ops. Models execution with IO but unbounded by the CPU pipeline.")
    parser.add_argument('--batches-per-step', type=int, default=1,
                        help="Number of batches to run per step (on the device)")
    parser.add_argument('--steps', type=int, default=1,
                        help="Number of steps to run (on the host)")
    parser.add_argument('--save-graph', action="store_true",
                        help="Save default graph to 'logs' directory (used by TensorBoard)")
    parser.add_argument('--convolution-options', type=str,
                        help='Set convolution options as a JSON string.')
    parser.add_argument('--matmul-options', type=str,
                        help='Set matrix multiplication options as a JSON string.')
    parser.add_argument('--enable-half-partials', action="store_true", default=False,
                        help="Use half (fp16) partials for both convolutions and matmuls")
    parser.add_argument('--shards', type=int, default=1,
                        help="Select a number of IPUs to split across")
    parser.add_argument('--device-id', type=int, default=-1,
                        help="Select a device")
    parser.add_argument('--use-zero-values', action="store_true",
                        help="If True weights and input will be initialised to zeros (otherwise random data)")
    parser.add_argument('--replicas', type=int, default=1,
                        help="Number of IPUs to replicate input data across")
    parser.add_argument('--report-hw-cycle-count', action="store_true",
                        help="Report cycle counts for each rsess.run call.")
    # Benchmark Arguments
    benchmark.add_args(parser)

    opts = parser.parse_args()

    if "TF_POPLAR_FLAGS" not in os.environ:
        os.environ["TF_POPLAR_FLAGS"] = ""

    if not opts.use_generated_data:
        if opts.use_zero_values:
            initType = "0"
        else:
            initType = "random"
        os.environ["TF_POPLAR_FLAGS"] += " --use_synthetic_data --synthetic_data_initializer=" + initType

    if opts.report_hw_cycle_count:
        os.environ["TF_POPLAR_FLAGS"] += " --log_cycle_count=1"

    # Should change this to a dictionary
    return opts


def get_config(opts):
    """Builds ipu_options"""

    config = IPUConfig()
    config.device_connection.type = DeviceConnectionType.ON_DEMAND

    if opts.device_id == -1:
        config.auto_select_ipus = opts.shards*opts.replicas
    else:
        config.select_ipus = opts.device_id

    if opts.convolution_options:
        config.convolutions.poplar_options = json.loads(opts.convolution_options)

    if opts.matmul_options:
        config.matmuls.poplar_options = json.loads(opts.matmul_options)

    if opts.enable_half_partials:
        config.matmuls.poplar_options = {"partialsType": 'half'}
        config.convolutions.poplar_options = {"partialsType": 'half'}
    return config
