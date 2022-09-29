#!/usr/bin/env python3
# Copyright (c) 2020 Graphcore Ltd. All rights reserved.
import argparse
import sys
import numpy as np
import tensorflow as tf
from tensorflow.python.ipu import ipu_compiler, scopes, config
from tensorflow.python.framework import errors

tf.compat.v1.disable_v2_behavior()


def device_connection_type(value):
    dcts = {
        "ALWAYS": config.DeviceConnectionType.ALWAYS,
        "ON_DEMAND": config.DeviceConnectionType.ON_DEMAND,
        "PRE_COMPILE": config.DeviceConnectionType.PRE_COMPILE,
        "NEVER": config.DeviceConnectionType.NEVER,
    }
    return dcts.get(value)


def my_graph(pa, pb, pc):
    o1 = pa + pb
    o2 = pa + pc
    result = o1 + o2
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--connection_type",
        choices=["ALWAYS", "ON_DEMAND", "PRE_COMPILE", "NEVER"],
        help="Specify connection type",
    )
    parser.set_defaults(connection_type="ALWAYS")
    opts = parser.parse_args()

    with tf.device("cpu"):
        pa = tf.compat.v1.placeholder(np.float32, [2], name="a")
        pb = tf.compat.v1.placeholder(np.float32, [2], name="b")
        pc = tf.compat.v1.placeholder(np.float32, [2], name="c")

    # Create the IPU section of the graph.
    with scopes.ipu_scope("/device:IPU:0"):
        out = ipu_compiler.compile(my_graph, [pa, pb, pc])

    # Define the feed_dict input data.
    fd = {pa: [1.0, 1.0], pb: [0.0, 1.0], pc: [1.0, 5.0]}

    # Connection type from options.
    connection_type = device_connection_type(opts.connection_type)

    cfg = config.IPUConfig()
    cfg.auto_select_ipus = 1
    if opts.connection_type in ["NEVER", "PRE_COMPILE"]:
        cfg.device_connection.type = connection_type
        cfg.device_connection.version = "ipu2"
    else:
        cfg.device_connection.type = connection_type
    cfg.configure_ipu_system()

    # Run the session.
    # If running with DeviceConnectionType.NEVER then anticipate the
    # specific exception with message "configured for compilation only".
    with tf.compat.v1.Session() as sess:
        try:
            result = sess.run(out, fd)
            print(result)
        except tf.errors.InvalidArgumentError as invalid_arg_exception:
            if (connection_type == config.DeviceConnectionType.NEVER) and (
                "configured for compilation only" in invalid_arg_exception.message
            ):
                print("Compiled")
                pass
            else:
                print(f"ERROR: {invalid_arg_exception.message}")
        except:
            general_exception = sys.exc_info()[0]
            print(f"ERROR: {general_exception}")


if __name__ == "__main__":
    main()
