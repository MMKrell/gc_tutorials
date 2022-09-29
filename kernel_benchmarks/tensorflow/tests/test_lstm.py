# Copyright (c) 2020 Graphcore Ltd. All rights reserved.
from pathlib import Path

import pytest

# NOTE: The import below is dependent on 'pytest.ini' in the root of
# the repository
import tutorials_tests.testing_util as testing_util

working_path = Path(__file__).parent.parent


"""High-level integration tests for TensorFlow LSTM synthetic benchmarks"""


@pytest.mark.category1
def test_help():
    testing_util.run_command("python3 lstm.py -h", working_path, "usage: lstm.py")


@pytest.mark.category1
@pytest.mark.ipus(1)
def test_default():
    testing_util.run_command("python3 lstm.py", working_path, [r"(\w+.\w+) items/sec"])


@pytest.mark.category1
@pytest.mark.ipus(1)
def test_inference_b256_s25_h1024():
    testing_util.run_command(
        "python3 lstm.py --batch-size 256 --timesteps 25 --hidden-size 1024 --use-generated-data --popnn",
        working_path,
        [r"(\w+.\w+) items/sec"],
    )


@pytest.mark.category1
@pytest.mark.ipus(1)
@pytest.mark.ipu_version("ipu2")
def test_inference_b128_s50_h1536():
    testing_util.run_command(
        "python3 lstm.py --batch-size 128 --timesteps 50 --hidden-size 1536 --popnn --save-graph",
        working_path,
        [r"(\w+.\w+) items/sec"],
    )


@pytest.mark.category1
@pytest.mark.ipus(1)
def test_inference_b64_s25_h2048():
    testing_util.run_command(
        "python3 lstm.py --batch-size 64 --timesteps 25 --hidden-size 2048 --steps=6 --popnn",
        working_path,
        [r"(\w+.\w+) items/sec"],
    )


@pytest.mark.category1
@pytest.mark.ipus(1)
def test_inference_b1024_s150_h256():
    testing_util.run_command(
        "python3 lstm.py --batch-size 1024 --timesteps 150 --hidden-size 256 --use-zero-values --batches-per-step 100",
        working_path,
        [r"(\w+.\w+) items/sec"],
    )


@pytest.mark.category1
@pytest.mark.ipus(1)
def test_train_b64_s25_h512():
    testing_util.run_command(
        "python3 lstm.py --batch-size 64 --timesteps 25 --hidden-size 512 --train --use-generated-data",
        working_path,
        [r"(\w+.\w+) items/sec"],
    )


@pytest.mark.category1
@pytest.mark.ipus(2)
def test_replicas():
    testing_util.run_command(
        "python3 lstm.py --replicas 2", working_path, [r"(\w+.\w+) items/sec"]
    )
