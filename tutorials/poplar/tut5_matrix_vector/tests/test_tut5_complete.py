# Copyright (c) 2020 Graphcore Ltd. All rights reserved.
from pathlib import Path
import pytest

# NOTE: The import below is dependent on 'pytest.ini' in the root of
# the repository
from tutorials_tests.testing_util import SubProcessChecker

working_path = Path(__file__).parent.parent.joinpath("complete")


class TestComplete(SubProcessChecker):

    def setUp(self):
        ''' Compile the CPU version of the tutorial code '''
        self.run_command("make tut5", working_path, [])

    @pytest.mark.category1
    def test_run_complete(self):
        ''' Check that the complete version of the tutorial code runs'''

        self.run_command("./tut5 40 50",
                         working_path,
                         ["Multiplying matrix of size 40x50 by vector of size 50",
                          "Multiplication result OK"])
