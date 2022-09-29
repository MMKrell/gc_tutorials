<!-- Copyright (c) 2020 Graphcore Ltd. All rights reserved. -->
# PopART Sharding example

This example shows how to shard a model on multiple IPUs using PopART.


## File structure

* `multi_ipu.py` The main PopART file showcasing sharding.
* `test_multi_ipu_popart.py` Test script.
* `README.md` This file.

## How to use this example

1) Prepare the environment.

   Install the Poplar SDK following the instructions in the Getting Started guide for your IPU system. Make sure to source the `enable.sh`
    scripts for Poplar and PopART.

2) Run the program. Note that the PopART Python API only supports Python 3.

    python3 multi_ipu.py


### Options
Run multi_ipu.py with `-h` option to list all the command line options.
