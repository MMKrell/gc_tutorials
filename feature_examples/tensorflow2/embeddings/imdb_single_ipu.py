# Copyright (c) 2020 Graphcore Ltd. All rights reserved.
import tensorflow as tf

from tensorflow.python import ipu

from ipu_tensorflow_addons.keras.layers import Embedding, LSTM
from tensorflow.keras.layers import Dense
from tensorflow.keras.layers import Input
from tensorflow.keras.datasets import imdb
from tensorflow.keras.preprocessing import sequence
from tensorflow.keras.optimizers import Adam

if tf.__version__[0] != "2":
    raise ImportError("TensorFlow 2 is required for this example")

max_features = 20000
minibatch_size = 32


# Define the dataset.
def get_dataset():
    (x_train, y_train), (_, _) = imdb.load_data(num_words=max_features)

    x_train = sequence.pad_sequences(x_train, maxlen=80)

    ds = tf.data.Dataset.from_tensor_slices((x_train, y_train))
    ds = ds.repeat()
    ds = ds.map(lambda x, y: (x, tf.cast(y, tf.int32)))
    ds = ds.batch(minibatch_size, drop_remainder=True)
    return ds


# Define the model.
def get_model():
    input_layer = Input(shape=(80), dtype=tf.int32, batch_size=minibatch_size)

    x = Embedding(max_features, 128)(input_layer)
    x = LSTM(128, dropout=0.2)(x)
    x = Dense(16, activation="relu")(x)
    x = Dense(1, activation="sigmoid")(x)

    return tf.keras.Model(input_layer, x)


def main():
    # Configure IPUs.
    cfg = ipu.config.IPUConfig()
    cfg.auto_select_ipus = 1
    cfg.configure_ipu_system()

    # Set up IPU strategy.
    strategy = ipu.ipu_strategy.IPUStrategy()
    with strategy.scope():

        model = get_model()

        model.compile(
            steps_per_execution=384, loss="binary_crossentropy", optimizer=Adam(0.005)
        )
        model.fit(get_dataset(), steps_per_epoch=768, epochs=3)


if __name__ == "__main__":
    main()
