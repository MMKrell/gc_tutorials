# Copyright (c) 2019 Graphcore Ltd. All rights reserved.
# Copyright (c) 2018 The TensorFlow Probability Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# The original code has been modified by Graphcore Ltd.

"""
Derived from
https://www.tensorflow.org/probability/api_docs/python/tfp/mcmc/HamiltonianMonteCarlo
"""
import tensorflow.compat.v1 as tf
from tensorflow.contrib.compiler import xla
import tensorflow_probability as tfp
import time
import inspect
import os
import argparse
from tensorflow.python.ipu import config, utils
import sys
from tensorflow.python import ipu


# Target distribution is proportional to: `exp(-x (1 + x))`.
def unnormalized_log_prob(x):
    return -x - x**2.


# Run single HMC step repeatedly
def run_single_steps(hmc, hmc_steps):
    def _step(i, state):
        new_state, _ = hmc.one_step(state, hmc.bootstrap_results(state))
        return [i + 1, new_state]

    _, s = tf.while_loop(cond=lambda i, _: i < hmc_steps,
                         body=_step,
                         loop_vars=[tf.constant(0), 1.])

    return s


if __name__ == '__main__':
    # Add benchmark module to path
    cwd = os.path.dirname(os.path.abspath(inspect.stack()[0][1]))
    sys.path.insert(1, os.path.join(cwd, '..', '..', 'utils',
                                    'benchmarks', 'tensorflow'))
    import benchmark

    parser = argparse.ArgumentParser(description='Synthetic Benchmarks in TensorFlow', formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--steps', type=int, default=100,
                        help="Number of steps to run (on the host)")
    parser.add_argument('--hmc-steps', type=int, default=10000,
                        help='Number of inner steps to run HMC (on the device)')
    parser.add_argument('--leapfrog-steps', type=int, default=5,
                        help='Number of steps to run the leapfrog integrator for')
    parser.add_argument('--save-graph', action="store_true",
                        help="Save default graph to 'logs' directory (used by TensorBoard)")
    options = parser.parse_args()

    # Initialize the HMC transition kernel.
    hmc = tfp.mcmc.HamiltonianMonteCarlo(
        target_log_prob_fn=unnormalized_log_prob,
        num_leapfrog_steps=options.leapfrog_steps,
        step_size=1.)

    with ipu.scopes.ipu_scope('/device:IPU:0'):
        ss = xla.compile(lambda: run_single_steps(hmc, options.hmc_steps), ())

    # Dump the graph to a logdir
    if options.save_graph:
        writer = tf.summary.FileWriter(os.path.join(os.path.dirname(os.path.realpath(__file__)), 'logs', time.strftime('%Y%m%d_%H%M%S_%Z')))
        writer.add_graph(tf.get_default_graph())

    cfg = config.IPUConfig()
    cfg.auto_select_ipus = 1
    cfg.configure_ipu_system()

    print(" Hamilton Monte-Carlo Synthetic benchmark.\n"
          " Inner steps {}.\n"
          " Leapfrop steps {}.\n"
          .format(options.hmc_steps, options.leapfrog_steps))

    conf = tf.ConfigProto(log_device_placement=True)
    with tf.Session(config=conf) as sess:
        utils.move_variable_initialization_to_cpu()
        sess.run(tf.global_variables_initializer())

        # Warmup
        print("Compiling and Warmup...")
        start = time.time()
        sess.run(ss)
        duration = time.time() - start
        print("Duration: {:.3f} seconds\n".format(duration))

        print("Executing...")
        t_total = 0.
        hmc_steps_per_sec = 0

        print('Running HMC.')
        for itr in range(options.steps):
            # HMC
            t_bef = time.time()
            state_out = sess.run(ss)
            duration = time.time() - t_bef
            t_total += duration

            print("{:<7.3} sec/itr.    {:5f} hmc steps/sec".format(duration, options.hmc_steps/duration))

        print(f'Avg time per step {t_total / float(options.steps * options.hmc_steps)}')
