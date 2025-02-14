# Copyright (c) 2020 Graphcore Ltd. All rights reserved.
import argparse
import os

import numpy as np
import tensorflow.compat.v1 as tf
from tensorflow.keras.datasets import mnist
from tensorflow.keras import layers

from tensorflow.python import ipu

tf.disable_eager_execution()
tf.disable_v2_behavior()


def parse_args():
    # Handle command line arguments
    pipeline_schedule_options = [p.name for p in ipu.pipelining_ops.PipelineSchedule]

    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=32, help="The batch size.")
    parser.add_argument(
        "--repeat-count",
        type=int,
        default=100,
        help="The number of times the pipeline will be executed for each step.",
    )
    parser.add_argument(
        "--epochs", type=float, default=50, help="Total number of epochs to train for."
    )
    parser.add_argument(
        "--learning-rate",
        type=float,
        default=0.01,
        help="The learning rate used with stochastic gradient descent.",
    )
    parser.add_argument(
        "--gradient-accumulation-count",
        type=int,
        default=16,
        help="The number of times each pipeline stage will be executed.",
    )
    parser.add_argument(
        "--pipeline-schedule",
        type=str,
        default="Grouped",
        choices=pipeline_schedule_options,
        help="Pipelining schedule. In the 'Grouped' schedule the forward passes"
        " are grouped together, and the backward passes are grouped together. "
        "With 'Interleaved' the forward and backward passes are interleaved. "
        "'Sequential' mimics a non-pipelined execution.",
    )
    parser.add_argument(
        "--synthetic-data",
        action="store_true",
        help="Use synthetic data instead of real images.",
    )
    parser.add_argument(
        "--run-single-step",
        action="store_true",
        help="Shorten the run for profiling: runs for a single step.",
    )
    args = parser.parse_args()
    return args


def create_dataset(args):
    # Prepare a tf dataset with mnist data
    train_data, _ = mnist.load_data()

    def normalise(x, y):
        return x.astype("float32") / 255.0, y.astype("int32")

    x_train, y_train = normalise(*train_data)

    def generator():
        return zip(x_train, y_train)

    types = (x_train.dtype, y_train.dtype)
    shapes = (x_train.shape[1:], y_train.shape[1:])

    n_examples = len(x_train)
    dataset = tf.data.Dataset.from_generator(generator, types, shapes)
    # Caching and prefetching are important to prevent the host data
    # feed from being the bottleneck for throughput
    dataset = dataset.batch(args.batch_size, drop_remainder=True)
    dataset = dataset.shuffle(n_examples)
    dataset = dataset.cache()
    dataset = dataset.repeat()
    dataset = dataset.prefetch(tf.data.experimental.AUTOTUNE)
    return n_examples, dataset


# The following is a schematic representation of the model defined in this example,
# which also shows how it is split across two IPUs:
# ------------------------------------ Model Definition ------------------------------------
#  <----------------------- ipu0 -----------------------> <------------- ipu1 ------------->
#
# inputs --|-- Dense --|-- Relu --|-- Dense --|-- Relu --|-- Dense--|-- SoftmaxCE --|-- Loss
#                 w0 --|                 w1 --|                w2 --|
#                 b0 --|                 b1 --|                b2 --|


def stage1(lr, images, labels):
    # Stage 1 of the pipeline. Will be placed on the first IPU
    partial = layers.Flatten()(images)
    partial = layers.Dense(256, activation=tf.nn.relu)(partial)
    partial = layers.Dense(128, activation=tf.nn.relu)(partial)
    return lr, partial, labels


def stage2(lr, partial, labels):
    # Stage 2 of the pipeline. Will be placed on the second IPU
    logits = layers.Dense(10)(partial)
    cross_entropy = tf.nn.sparse_softmax_cross_entropy_with_logits(
        labels=labels, logits=logits
    )
    loss = tf.reduce_mean(cross_entropy)
    return lr, loss


def optimizer_function(lr, loss):
    # Optimizer function used by the pipeline to automatically set up
    # the gradient accumulation and weight update steps
    optimizer = tf.train.GradientDescentOptimizer(lr)
    return ipu.pipelining_ops.OptimizerFunctionOutput(optimizer, loss)


def model(lr):
    # Defines a pipelined model which is split across two stages
    with tf.variable_scope("FCModel", use_resource=True):
        pipeline_op = ipu.pipelining_ops.pipeline(
            computational_stages=[stage1, stage2],
            gradient_accumulation_count=args.gradient_accumulation_count,
            repeat_count=args.repeat_count,
            inputs=[lr],
            infeed_queue=infeed_queue,
            outfeed_queue=outfeed_queue,
            optimizer_function=optimizer_function,
            pipeline_schedule=next(
                p
                for p in ipu.pipelining_ops.PipelineSchedule
                if args.pipeline_schedule == p.name
            ),
            outfeed_loss=True,
            name="Pipeline",
        )
    return pipeline_op


if __name__ == "__main__":
    args = parse_args()

    if args.synthetic_data:
        if "TF_POPLAR_FLAGS" in os.environ:
            os.environ["TF_POPLAR_FLAGS"] += " --use_synthetic_data"
        else:
            os.environ["TF_POPLAR_FLAGS"] = "--use_synthetic_data"

    n_examples, dataset = create_dataset(args)

    # Create the data queues from/to IPU
    infeed_queue = ipu.ipu_infeed_queue.IPUInfeedQueue(dataset)
    outfeed_queue = ipu.ipu_outfeed_queue.IPUOutfeedQueue()

    # With batch size BS, gradient accumulation count GAC and repeat count RPT,
    # at every step n = (BS * GAC * RPT) examples are used.
    # So in order to evaluate at least N total examples, do ceil(N / n) steps
    num_train_examples = int(args.epochs * n_examples)
    examples_per_step = (
        args.batch_size * args.gradient_accumulation_count * args.repeat_count
    )
    steps = ((num_train_examples - 1) // examples_per_step) + 1

    if args.run_single_step:
        steps = 1

    with tf.device("cpu"):
        lr = tf.placeholder(np.float32, [])

    with ipu.scopes.ipu_scope("/device:IPU:0"):
        compiled_model = ipu.ipu_compiler.compile(model, inputs=[lr])

    outfeed_op = outfeed_queue.dequeue()

    ipu.utils.move_variable_initialization_to_cpu()
    init_op = tf.global_variables_initializer()

    # Configure the IPU.
    # With pipelining, IPU-level profiling is needed to correctly visualise the execution trace.
    # For pipelined models either SNAKE or HOOF IPU selection orders are advised;
    # the latter works best when the first and last stage are on the same IPU.
    # For more information, see the API section of the Targeting the IPU from TensorFlow document:
    # https://docs.graphcore.ai/projects/tensorflow1-user-guide/en/3.0.0/tensorflow/api.html#tensorflow.python.ipu.config.SelectionOrder
    cfg = ipu.config.IPUConfig()
    cfg.auto_select_ipus = 2
    cfg.selection_order = ipu.config.SelectionOrder.SNAKE
    cfg.configure_ipu_system()

    with tf.Session() as sess:
        # Initialize
        sess.run(init_op)
        sess.run(infeed_queue.initializer)
        # Run
        for step in range(steps):
            sess.run(compiled_model, {lr: args.learning_rate})

            # Read the outfeed for the training losses
            losses = sess.run(outfeed_op)
            epoch = float(examples_per_step * step / n_examples)
            print(f"Epoch {epoch:.1f}, Mean loss: {np.mean(losses):.3f}")
