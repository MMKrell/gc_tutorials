# Copyright (c) 2022 Graphcore Ltd. All rights reserved.

from tensorflow.python.data.ops.dataset_ops import Dataset
from tensorflow.python.ipu import utils
from tensorflow.keras import layers
from tensorflow.python.ipu import config
from tensorflow.python.ipu import ipu_strategy
import numpy as np
import tensorflow as tf

# default data_format is 'channels_last'
dataset = Dataset.from_tensor_slices(
    np.random.uniform(size=(2, 128, 128, 3)).astype(np.float32)
)
dataset = dataset.batch(batch_size=2, drop_remainder=True)
dataset = dataset.cache()
dataset = dataset.repeat()
dataset = dataset.prefetch(tf.data.experimental.AUTOTUNE)


# Create a pipelined model which is split across two stages.
def my_model():
    return tf.keras.Sequential([layers.Conv2D(128, 1), layers.Conv2D(128, 1)])


cfg = config.IPUConfig()
cfg.auto_select_ipus = 2
cfg.configure_ipu_system()
utils.move_variable_initialization_to_cpu()


# Define the model under an IPU strategy scope
strategy = ipu_strategy.IPUStrategy()
with strategy.scope():
    model = my_model()

    model.set_pipeline_stage_assignment([0, 1])
    model.set_pipelining_options(gradient_accumulation_steps_per_replica=16)

    model.compile(steps_per_execution=10)
    model.predict(dataset, steps=2)
