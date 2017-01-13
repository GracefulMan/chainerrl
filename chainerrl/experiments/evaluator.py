from __future__ import print_function
from __future__ import unicode_literals
from __future__ import division
from __future__ import absolute_import
from builtins import range
from builtins import open
from builtins import str
from future import standard_library
standard_library.install_aliases()

import multiprocessing as mp
import os
import statistics
import time

import numpy as np


def eval_performance(env, agent, n_runs, max_episode_len=None,
                     explorer=None):
    scores = []
    for i in range(n_runs):
        obs = env.reset()
        done = False
        test_r = 0
        t = 0
        while not (done or t == max_episode_len):
            def greedy_action_func():
                return agent.act(obs)
            if explorer is not None:
                a = explorer.select_action(t, greedy_action_func)
            else:
                a = greedy_action_func()
            obs, r, done, info = env.step(a)
            test_r += r
            t += 1
        agent.stop_episode()
        # As mixing float and numpy float causes errors in statistics
        # functions, here every score is cast to float.
        scores.append(float(test_r))
        print('test_{}:'.format(i), test_r)
    mean = statistics.mean(scores)
    median = statistics.median(scores)
    if n_runs >= 2:
        stdev = statistics.stdev(scores)
    else:
        stdev = 0.
    return mean, median, stdev


def record_stats(outdir, values):
    with open(os.path.join(outdir, 'scores.txt'), 'a+') as f:
        print('\t'.join(str(x) for x in values), file=f)


def save_agent_model(agent, t, outdir, suffix=''):
    dirname = os.path.join(outdir, '{}{}'.format(t, suffix))
    agent.save(dirname)
    print('Saved the current model to {}'.format(dirname))


def update_best_model(agent, outdir, t, old_max_score, new_max_score):
    # Save the best model so far
    print('The best score is updated {} -> {}'.format(
        old_max_score, new_max_score))
    save_agent_model(agent, t, outdir)


class Evaluator(object):

    def __init__(self, agent, env, n_runs, eval_frequency,
                 outdir, max_episode_len=None, explorer=None,
                 step_offset=0):
        self.agent = agent
        self.env = env
        self.max_score = np.finfo(np.float32).min
        self.start_time = time.time()
        self.n_runs = n_runs
        self.eval_frequency = eval_frequency
        self.outdir = outdir
        self.max_episode_len = max_episode_len
        self.explorer = explorer
        self.step_offset = step_offset
        self.prev_eval_t = self.step_offset - self.step_offset % self.eval_frequency

        # Write a header line first
        with open(os.path.join(self.outdir, 'scores.txt'), 'w') as f:
            column_names = (('steps', 'elapsed', 'mean', 'median', 'stdev') +
                            self.agent.get_stats_keys())
            print('\t'.join(column_names), file=f)

    def evaluate_and_update_max_score(self, t):
        mean, median, stdev = eval_performance(
            self.env, self.agent, self.n_runs,
            max_episode_len=self.max_episode_len, explorer=self.explorer)
        elapsed = time.time() - self.start_time
        values = (t, elapsed, mean, median, stdev) + \
            self.agent.get_stats_values()
        record_stats(self.outdir, values)
        if mean > self.max_score:
            update_best_model(self.agent, self.outdir, t, self.max_score, mean)
            self.max_score = mean

    def evaluate_if_necessary(self, t):
        if t >= self.prev_eval_t + self.eval_frequency:
            self.evaluate_and_update_max_score(t)
            self.prev_eval_t = t - t % self.eval_frequency


class AsyncEvaluator(object):

    def __init__(self, n_runs, eval_frequency,
                 outdir, max_episode_len=None, explorer=None,
                 step_offset=0):

        self.start_time = time.time()
        self.n_runs = n_runs
        self.eval_frequency = eval_frequency
        self.outdir = outdir
        self.max_episode_len = max_episode_len
        self.explorer = explorer
        self.step_offset = step_offset

        # Values below are shared among processes
        self.prev_eval_t = mp.Value(
            'l', self.step_offset - self.step_offset % self.eval_frequency)
        self._max_score = mp.Value('f', np.finfo(np.float32).min)
        self.wrote_header = mp.Value('b', False)

        # Create scores.txt
        with open(os.path.join(self.outdir, 'scores.txt'), 'a'):
            pass

    @property
    def max_score(self):
        with self._max_score.get_lock():
            v = self._max_score.value
        return v

    def evaluate_and_update_max_score(self, t, env, agent):
        mean, median, stdev = eval_performance(
            env, agent, self.n_runs,
            max_episode_len=self.max_episode_len, explorer=self.explorer)
        elapsed = time.time() - self.start_time
        values = (t, elapsed, mean, median, stdev) + \
            agent.get_stats_values()
        record_stats(self.outdir, values)
        with self._max_score.get_lock():
            if mean > self._max_score.value:
                update_best_model(
                    agent, self.outdir, t, self._max_score.value, mean)
                self._max_score.value = mean

    def write_header(self, agent):
        with open(os.path.join(self.outdir, 'scores.txt'), 'w') as f:
            column_names = (('steps', 'elapsed', 'mean', 'median', 'stdev') +
                            agent.get_stats_keys())
            print('\t'.join(column_names), file=f)

    def evaluate_if_necessary(self, t, env, agent):
        necessary = False
        with self.prev_eval_t.get_lock():
            if t >= self.prev_eval_t.value + self.eval_frequency:
                necessary = True
                self.prev_eval_t.value += self.eval_frequency
        if necessary:
            with self.wrote_header.get_lock():
                if not self.wrote_header.value:
                    self.write_header(agent)
                    self.wrote_header.value = True
            self.evaluate_and_update_max_score(t, env, agent)
