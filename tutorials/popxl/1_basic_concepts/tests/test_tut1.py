# Copyright (c) 2022 Graphcore Ltd. All rights reserved.

import pytest
from pathlib import Path
import sys
from tutorials_tests import testing_util


@pytest.mark.category1
@pytest.mark.ipus(1)
def test_template(tmp_path):
    working_directory = Path(__file__).parent.parent
    out = testing_util.run_command_fail_explicitly(
        [sys.executable, "mnist_template.py"], working_directory
    )
    # Get accuracy
    accuracy_regex = r"Accuracy on test set: ([\d.]+)"
    accuracy = testing_util.parse_results_with_regex(out, accuracy_regex)[0][-1]
    assert accuracy > 90


@pytest.mark.category1
@pytest.mark.ipus(1)
def test_sst(tmp_path):
    working_directory = Path(__file__).parent.parent
    out = testing_util.run_command_fail_explicitly(
        [sys.executable, "mnist.py"], working_directory
    )
    # Get accuracy
    accuracy_regex = r"Accuracy on test set: ([\d.]+)"
    accuracy = testing_util.parse_results_with_regex(out, accuracy_regex)[0][-1]
    assert accuracy > 90
