# Copyright (c) 2020 Graphcore Ltd. All rights reserved.
"""Tutorial code to play around with graph recompilation and executable loading

Parameters to play around with are CACHING, NOMULTISESSION, PLACEHOLDER,
and SAMEBATCH. Some comments in the document refer to the underlying tutorial
in the documentation portal.

The code will print out what the expected behaviour should look like.
"""

import os
import numpy as np

from tensorflow.python import ipu
from tensorflow.python.ipu.scopes import ipu_scope
import tensorflow.compat.v1 as tf

tf.disable_v2_behavior()

# Consideration 0: Environment setup
CACHING = True  # Cache compiled graph. The folder is tmp_tutorial.
# Consideration 1, 3, 5: Graphs, Weights, Constants
# Use a placeholder that is handed over to the graph instead of a hard coded
# hyperparameter that might change between executions.
PLACEHOLDER = True
# Consideration 2: Batch size
SAMEBATCH = True  # Change the batch size between executions.

# Consideration 0: Environment setup
if "TF_POPLAR_FLAGS" in os.environ and not CACHING:
    os.environ["TF_POPLAR_FLAGS"] = ""
else:
    os.environ["TF_POPLAR_FLAGS"] = "--executable_cache_path=tmp_tutorial"
if "POPLAR_LOG_LEVEL" not in os.environ or os.environ["POPLAR_LOG_LEVEL"] != "INFO":
    print("Setting POPLAR_LOG_LEVEL to INFO for graph compilation information.")
    os.environ["POPLAR_LOG_LEVEL"] = "INFO"

# Consideration 6
os.environ["XLA_FLAGS"] = f"--xla_dump_to=tmp_xla_{np.random.randint(2, 101)} "
os.environ["XLA_FLAGS"] += " --xla_dump_hlo_pass_re=forward-allocation "
os.environ["XLA_FLAGS"] += " --xla_hlo_graph_sharding_color "
os.environ["XLA_FLAGS"] += " --xla_dump_hlo_as_text "

# Configure arguments for targeting the IPU
cfg = ipu.config.IPUConfig()
cfg.auto_select_ipus = 1
cfg.configure_ipu_system()

with tf.device("cpu"):
    pa = tf.placeholder(np.float32, [None, 2], name="a")
    pb = tf.placeholder(np.float32, [None, 2], name="b")
    pc = tf.placeholder(np.float32, [None, 2], name="c")

if PLACEHOLDER:
    mult = tf.placeholder(np.float32, [], name="multiplier")
else:
    mult = np.random.uniform(0, 1)


def basic_graph(pa, pb, pc):
    # Do basic addition with tensors
    o1 = pa + pb
    o2 = pa + pc
    simple_graph_output = mult * (o1 + o2)
    return simple_graph_output


with ipu_scope("/device:IPU:0"):
    comp_graph = basic_graph(pa, pb, pc)

print("\nWarm up & Caching Test: ")
print("No compilation after first execution expected but executable load. \n")
with tf.Session() as sess1, tf.Session() as sess2:
    # Run the graph through the session feeding it an arbitrary dictionary
    if PLACEHOLDER:
        result0 = sess1.run(
            comp_graph,
            feed_dict={
                pa: [[1.0, 1.0]],
                pb: [[0.0, 1.0]],
                pc: [[1.0, 5.0]],
                mult: 10.0,
            },
        )
    else:
        result0 = sess1.run(
            comp_graph,
            feed_dict={
                pa: [[1.0, 1.0]],
                pb: [[0.0, 1.0]],
                pc: [[1.0, 5.0]],
            },
        )

# Consideration 1, 3, 5: Graphs, Weights, Constants
m = np.random.uniform(0, 1)
if not PLACEHOLDER:
    mult = m
    with ipu_scope("/device:IPU:0"):
        comp_graph = basic_graph(pa, pb, pc)

with tf.Session() as sess1, tf.Session() as sess2:
    print("\nPlaceholder test. ")
    print("No recompilation but executable switch should occur.\n")
    # Run the graph through the session feeding it an arbitrary dictionary
    if PLACEHOLDER:
        result1 = sess1.run(
            comp_graph,
            feed_dict={pa: [[1.0, 1.0]], pb: [[0.0, 1.0]], pc: [[1.0, 5.0]], mult: m},
        )
    else:
        result1 = sess1.run(
            comp_graph,
            feed_dict={
                pa: [[1.0, 1.0]],
                pb: [[0.0, 1.0]],
                pc: [[1.0, 5.0]],
            },
        )

    # Consideration 2: Batch size
    if SAMEBATCH:
        bs = 1
    else:
        bs = np.random.randint(2, 101)
    print(f"\nBatch Size Test with batch size {bs}.")
    print("No recompilation or executable switch should occur.")
    print("Batch size should be the original 1.\n")
    if PLACEHOLDER:
        result2 = sess2.run(
            comp_graph,
            feed_dict={
                pa: [[1.0, 1.0]] * bs,
                pb: [[0.0, 1.0]] * bs,
                pc: [[1.0, 5.0]] * bs,
                mult: m,
            },
        )
    else:
        result3 = sess2.run(
            comp_graph,
            feed_dict={
                pa: [[1.0, 1.0]] * bs,
                pb: [[0.0, 1.0]] * bs,
                pc: [[1.0, 5.0]] * bs,
            },
        )

    print("\nFirst two results should be different (different multiplier).\n")
    print("Caching/warm up test:\t", result0)
    print()
    print("Placeholder test:    \t", result1)
    print()
    print()
    if bs > 1:
        print("Batch size test:     \t", result2[:2], "...")
    else:
        print("Batch size test:     \t", result2)
