#!/usr/bin/env python
# Copyright (c) 2019 Graphcore Ltd. All rights reserved.

"""
Benchmark a single GRU layer.

The Items/sec reported at the end of the benchmark is based on wall time.

Run with -h or --help for options.
"""
import inspect
import os
import sys
import tensorflow as tf
from tensorflow.python.ipu import utils
from ipu_tensorflow_addons.layers import rnn_ops


def gru(opts, inputs):
    if opts.popnn:
        # PopnnGRU uses a direct PopLibs implementation
        gru_cell = rnn_ops.PopnnGRU(opts.hidden_size, dtype=inputs.dtype)
        # The input is [timesteps, batch_size, input_size],
        # where input_size is equal to hidden_size
        return gru_cell(inputs, training=opts.train)
    else:
        # The input for GRUCell is instead [batch_size, input_size],
        # where input_size is equal to hidden_size
        gru_cell = tf.nn.rnn_cell.GRUCell(opts.hidden_size)
        # Dynamic_rnn creates a loop that passes slices across timesteps to GRUCell.
        # This is expanded in tensorflow creating a less optimal solution than PopnnGRU.
        return tf.nn.dynamic_rnn(
            cell=gru_cell, inputs=inputs, dtype=inputs.dtype, time_major=True
        )


def inputs(opts, index):
    value = tf.cast(index, tf.float16)
    return {
        "inputs": tf.broadcast_to(
            value, [opts.timesteps, opts.batch_size, opts.input_size]
        ),
        "labels": tf.broadcast_to(
            value, [opts.timesteps, opts.batch_size, opts.hidden_size]
        ),
    }


def graph_builder(opts, inputs):
    output, __ = gru(opts, inputs["inputs"])

    if opts.train:
        # Loss is the mean across all timesteps:
        loss = tf.reduce_mean(
            tf.nn.softmax_cross_entropy_with_logits_v2(
                logits=output, labels=inputs["labels"]
            )
        )
        optimiser = tf.train.GradientDescentOptimizer(0.01)
        with tf.variable_scope("train", reuse=tf.AUTO_REUSE):
            # We need to ensure that the train op is executed as part of
            # the benchmarking loop by maintaining a step variable and
            # forcing a control dependency between it and the train op:
            global_step = tf.get_variable("step_control", dtype=tf.int32, shape=[])
            grads_and_vars = optimiser.compute_gradients(loss, tf.trainable_variables())
            train = optimiser.apply_gradients(grads_and_vars, global_step)
            with tf.control_dependencies([train]):
                global_step = tf.identity(global_step)
            return global_step
    return output


def initializer():
    utils.move_variable_initialization_to_cpu()
    return tf.global_variables_initializer()


def add_args(parser):
    parser.add_argument(
        "--batch-size", default=1, type=int, help="Number of inputs in a mini-batch"
    )
    parser.add_argument(
        "--timesteps", default=5, type=int, help="Number of recurrent steps"
    )
    parser.add_argument("--hidden-size", default=128, type=int, help="GRU hidden size")
    parser.add_argument("--input-size", default=128, type=int, help="GRU input size")
    parser.add_argument(
        "--train",
        action="store_true",
        dest="train",
        help="Compute loss and optimization pass (default)",
    )
    parser.add_argument(
        "--no-train",
        action="store_false",
        dest="train",
        help="No loss or optimisation pass will be computed",
    )
    parser.add_argument(
        "--popnn",
        action="store_true",
        help="Use tf.python.ipu.rnn_ops.PopnnGRU to target PopLibs implementation directly",
    )
    parser.set_defaults(train=False, batches_per_step=1000, steps=5)
    return parser


def iteration_report(opts, time):
    rate = opts.batch_size * opts.batches_per_step * opts.replicas / time
    return f"{rate:5f} items/sec"


if __name__ == "__main__":
    # Add benchmark module to path
    cwd = os.path.dirname(os.path.abspath(inspect.stack()[0][1]))
    sys.path.insert(
        1, os.path.join(cwd, "..", "..", "utils", "benchmarks", "tensorflow")
    )
    import benchmark

    module = benchmark.Benchmark(
        graph_builder, inputs, initializer, add_args, iteration_report
    )

    options = benchmark.parse_opts(module, False)

    if options.shards > 1:
        raise NotImplementedError(
            "--shards option has not been implemented with this example"
        )

    # Log Benchmark Message
    print(
        f"GRU layer {'Training' if options.train else 'Inference'} Synthetic benchmark.\n"
        f" Batch size {options.batch_size}.\n"
        f" Batches per Step {options.batches_per_step}.\n"
        f" Steps {options.steps}.\n"
        f" Hidden size {options.hidden_size}.\n"
        f" Timesteps {options.timesteps}.\n"
    )

    benchmark.run(module, options)
