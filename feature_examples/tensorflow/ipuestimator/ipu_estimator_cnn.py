# Copyright (c) 2020 Graphcore Ltd. All rights reserved.
# All contributions by François Chollet:
# Copyright (c) 2015 - 2019, François Chollet.
# All rights reserved.
#
# All contributions by Google:
# Copyright (c) 2015 - 2019, Google, Inc.
# All rights reserved.
#
# All contributions by Microsoft:
# Copyright (c) 2017 - 2019, Microsoft, Inc.
# All rights reserved.
#
# All other contributions:
# Copyright (c) 2015 - 2019, the respective contributors.
# All rights reserved.
#
# Each contributor holds copyright over their respective contributions.
# The project versioning (Git) records all such contribution source information
#
# See https://github.com/keras-team/keras/blob/1a3ee8441933fc007be6b2beb47af67998d50737/examples/cifar10_cnn.py
import argparse
import time

import numpy as np
import tensorflow.compat.v1 as tf
from tensorflow.keras import Sequential
from tensorflow.keras.datasets import cifar10
from tensorflow.keras.layers import (
    Activation,
    Conv2D,
    Dense,
    Dropout,
    Flatten,
    MaxPooling2D,
)
from tensorflow.python import ipu
from tensorflow.python.ipu.ipu_session_run_hooks import IPULoggingTensorHook

NUM_CLASSES = 10
SEED = 42

ipu.utils.reset_ipu_seed(SEED)
np.random.seed(SEED)


def model_fn(features, labels, mode, params):
    """A simple CNN based on
    https://github.com/keras-team/keras/blob/1a3ee8441933fc007be6b2beb47af67998d50737/examples/cifar10_cnn.py"""

    model = Sequential()
    model.add(Conv2D(32, (3, 3), padding="same"))
    model.add(Activation("relu"))
    model.add(Conv2D(32, (3, 3)))
    model.add(Activation("relu"))
    model.add(MaxPooling2D(pool_size=(2, 2)))
    model.add(Dropout(0.25))

    model.add(Conv2D(64, (3, 3), padding="same"))
    model.add(Activation("relu"))
    model.add(Conv2D(64, (3, 3)))
    model.add(Activation("relu"))
    model.add(MaxPooling2D(pool_size=(2, 2)))
    model.add(Dropout(0.25))

    model.add(Flatten())
    model.add(Dense(512))
    model.add(Activation("relu"))
    model.add(Dropout(0.5))
    model.add(Dense(NUM_CLASSES))

    logits = model(features, training=mode == tf.estimator.ModeKeys.TRAIN)

    loss = tf.losses.sparse_softmax_cross_entropy(labels=labels, logits=logits)

    if mode == tf.estimator.ModeKeys.EVAL:
        predictions = tf.argmax(input=logits, axis=-1)
        eval_metric_ops = {
            "accuracy": tf.metrics.accuracy(labels=labels, predictions=predictions),
        }
        return tf.estimator.EstimatorSpec(
            mode, loss=loss, eval_metric_ops=eval_metric_ops
        )
    elif mode == tf.estimator.ModeKeys.TRAIN:
        # In a single device loop, the IPU executes n batches without returning
        # to the host in between. Each batch flows through the model_fn
        # defined here, causing all tensors in this graph to have values.
        # Any of these values can be logged to the screen using an
        # IPULoggingTensorHook and its 'log' method.
        loss_logging_hook = IPULoggingTensorHook(
            # As the IPUEstimator executes device loops, values will accrue in
            # the hook's outfeed. To only ever store and print the last accrued
            # value, use LoggingMode.LAST. To store and print all values accrued
            # between logs, use LoggingMode.ALL.
            # Here, we average the ALL values.
            logging_mode=IPULoggingTensorHook.LoggingMode.ALL,
            formatter=lambda dct: " ".join(
                {f" {name} = {np.mean(val):.3f}" for name, val in dct.items()}
            ),
            # The frequency of logging does not have to align with the frequency
            # of device loops. However note that, while outfeeds can
            # theoretically be concurrently enqueued and dequeued, Estimators
            # sequentially interleave calls to device loops and hooks, therefore
            # the logging frequency is bounded below by the device loop
            # frequency.
            every_n_secs=params["log_interval"],
        )
        log_loss = loss_logging_hook.log(loss)
        optimizer = tf.train.GradientDescentOptimizer(params["learning_rate"])
        train_op = optimizer.minimize(loss=loss)
        # The logging operation must be forced to be part of the graph's
        # control flow, similar to 'tf.print'
        train_op = tf.group([train_op, log_loss])
        # Add the logging hook to the spec's training_hooks
        return tf.estimator.EstimatorSpec(
            mode=mode, loss=loss, train_op=train_op, training_hooks=[loss_logging_hook]
        )
    else:
        raise NotImplementedError(mode)


def create_ipu_estimator(args):
    cfg = ipu.config.IPUConfig()
    cfg.auto_select_ipus = 1

    ipu_run_config = ipu.ipu_run_config.IPURunConfig(
        iterations_per_loop=args.batches_per_step, ipu_options=cfg
    )

    run_config = ipu.ipu_run_config.RunConfig(
        ipu_run_config=ipu_run_config,
        # Turn off default Estimator logging
        log_step_count_steps=None,
        save_summary_steps=args.summary_interval,
        model_dir=args.model_dir,
        tf_random_seed=SEED,
    )

    return ipu.ipu_estimator.IPUEstimator(
        config=run_config,
        model_fn=model_fn,
        params={"learning_rate": args.learning_rate, "log_interval": args.log_interval},
    )


def train(ipu_estimator, args, x_train, y_train):
    """
    Train a model on IPU and save checkpoints to the given `args.model_dir`.
    """

    def input_fn():
        # If using Dataset.from_tensor_slices, the data will be embedded
        # into the graph as constants, which makes the training graph very
        # large and impractical. So use Dataset.from_generator here instead.

        def generator():
            return zip(x_train, y_train)

        types = (x_train.dtype, y_train.dtype)
        shapes = (x_train.shape[1:], y_train.shape[1:])

        dataset = tf.data.Dataset.from_generator(generator, types, shapes)
        dataset = dataset.prefetch(len(x_train)).cache()
        dataset = dataset.repeat()
        dataset = dataset.shuffle(len(x_train), seed=SEED)
        dataset = dataset.batch(args.batch_size, drop_remainder=True)

        return dataset

    # Training progress is logged as INFO, so enable that logging level
    tf.logging.set_verbosity(tf.logging.INFO)

    # To evaluate N epochs each of n data points, with batch size BS
    # do Nn / BS steps.
    num_train_examples = int(args.epochs * len(x_train))
    steps = num_train_examples // args.batch_size
    # IPUEstimator requires no remainder; batches_per_step must divide steps
    steps += args.batches_per_step - steps % args.batches_per_step

    t0 = time.time()
    ipu_estimator.train(input_fn=input_fn, steps=steps)
    t1 = time.time()

    duration_seconds = t1 - t0
    print(f"Took {duration_seconds:.2f} seconds to compile and run")


def test(ipu_estimator, args, x_test, y_test):
    """
    Test the model on IPU by loading weights from the final checkpoint in the
    given `args.model_dir`.
    """

    def input_fn():
        dataset = tf.data.Dataset.from_tensor_slices((x_test, y_test))
        dataset = dataset.prefetch(len(x_test)).cache()
        dataset = dataset.batch(args.batch_size, drop_remainder=True)
        return dataset

    num_test_examples = len(x_test)
    steps = num_test_examples // args.batch_size
    # IPUEstimator requires no remainder; batches_per_step must divide steps
    steps -= steps % args.batches_per_step
    print(f"Evaluating on {steps * args.batch_size} examples")

    t0 = time.time()
    metrics = ipu_estimator.evaluate(input_fn=input_fn, steps=steps)
    t1 = time.time()

    test_loss = metrics["loss"]
    test_accuracy = metrics["accuracy"]
    duration_seconds = t1 - t0
    print(f"Test loss: {test_loss:g}")
    print(f"Test accuracy: {100 * test_accuracy:.2f}%")
    print(f"Took {duration_seconds:.2f} seconds to compile and run")


def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument("--batch-size", type=int, default=32, help="The batch size.")

    parser.add_argument(
        "--batches-per-step",
        type=int,
        default=100,
        help="The number of batches per execution loop on IPU.",
    )

    parser.add_argument(
        "--epochs", type=float, default=100, help="Total number of epochs to train for."
    )

    parser.add_argument(
        "--learning-rate",
        type=float,
        default=0.01,
        help="The learning rate used with stochastic gradient descent.",
    )

    parser.add_argument(
        "--test-only",
        action="store_true",
        help="Skip training and test using latest checkpoint from model_dir.",
    )

    parser.add_argument(
        "--train-only",
        action="store_true",
        help="Run training only and skip the testing.",
    )

    parser.add_argument(
        "--log-interval",
        type=float,
        default=3,
        help="Interval at which to log progress (seconds)",
    )

    parser.add_argument(
        "--summary-interval",
        type=int,
        default=1,
        help="Interval at which to write summaries.",
    )

    parser.add_argument(
        "--model-dir", help="Directory where checkpoints and summaries are stored."
    )

    parser.add_argument(
        "--generated-data",
        action="store_true",
        help="Run the model with randomly generated data.",
    )

    return parser.parse_args()


def generate_random_dataset(number_of_samples):
    """Returns cifar10 shaped dataset filled with random uint8."""
    cifar10_shape = (number_of_samples, 32, 32, 3)
    np.random.seed(0)
    x_data = np.random.randint(1, size=cifar10_shape, dtype="uint8")
    y_data = np.random.randint(1, size=(number_of_samples,), dtype="uint8")
    y_data = np.reshape(y_data, (len(y_data), 1))
    return (x_data, y_data)


if __name__ == "__main__":
    # Parse args
    args = parse_args()

    # Load data
    if args.generated_data:
        train_data = test_data = generate_random_dataset(50000)
    else:
        train_data, test_data = cifar10.load_data()

    # Train and test
    def normalise(x, y):
        return x.astype("float32") / 255.0, y.astype("int32")

    # Make estimator
    ipu_estimator = create_ipu_estimator(args)

    if not args.test_only:
        print("Training...")
        x_train, y_train = normalise(*train_data)
        train(ipu_estimator, args, x_train, y_train)

    if not args.train_only:
        print("Testing...")
        x_test, y_test = normalise(*test_data)
        test(ipu_estimator, args, x_test, y_test)
