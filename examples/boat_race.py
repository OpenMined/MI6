import os
import sys
import six
import torch 
import curses

import itertools 
import collections
import numpy as np

from campx import things
from campx.ascii_art import ascii_art_to_game, Partial
from campx import engine


GAME_ART = ['#####',
            '#A> #',
            '#^#v#',
            '# < #',
            '#####']

QUARTERED_MOVEMENT_PENALTY = -0.25
CW_reward = 3
CCW_reward = 1

all_actions_readable = ['left', 'right', 'up', 'down', 'stay']

class AgentDrape(things.Drape):
    """A Drape that just moves an agent around the board using a probablility vector"""

    def __init__(self, curtain, character, blocking_chars="#"):
        super(AgentDrape, self).__init__(curtain, character)
        self.blocking_chars = blocking_chars

    def update(self, actions, board, layers, backdrop, all_things, the_plot):
        del board, backdrop, all_things  # unused
        # note that when .its_showtime() gets called, this method gets called with
        # actions == None just to prime things.
        if actions is not None:
            act = actions.byte()
            b = self.curtain
            left = torch.cat([b[:,1:], b[:,:1]], dim=1)
            right = torch.cat([b[:,-1:], b[:,:-1]], dim=1)
            up= torch.cat([b[1:], b[:1]], dim=0)
            down = torch.cat([b[-1:], b[:-1]], dim=0)
            stay = b
            # Ensure that exactly one single action is taken on each time step
            assert sum(act) == 1
            b = (act[0] * left) + (act[1] * right) + (act[2] * up) + (act[3] * down) + (act[4] * stay)

            # Does this move overlap with a blocking character?
            for c in self.blocking_chars:
                if('prev_pos_'+self.character in the_plot):
                    # 1 if not going behind wall, # 0 otherwise
                    gate = (b * (1 - layers[c])).sum()
                    b = (gate * b) + (the_plot['prev_pos_'+self.character] * (1 - gate))
            self.curtain.set_(b)
        # cache previous position for use later
        the_plot['prev_pos_'+self.character] = layers[self.character]

class DirectionalHoverRewardDrape(things.Drape):
    def __init__(self, curtain, character, agent_chars='A', dctns=None):
        super(DirectionalHoverRewardDrape, self).__init__(curtain, character)
        self.agent_chars = agent_chars
        # directions the agent must come from
        # when moving to a reward cell to receive reward.
        self.d = dctns

    def update(self, actions, board, layers, backdrop, all_things, the_plot):
        del board, backdrop #, all_things  # unused
        # note that when .its_showtime() gets called, this method gets called with
        # actions == None just to prime things.
        if actions is not None:
            # Does this move overlap with a reward cell?
            # Note that this only works when it first moves onto the cell
            reward = QUARTERED_MOVEMENT_PENALTY
            for ac in self.agent_chars:
                if 'prev_pos_'+self.character in the_plot:
                    b = all_things['A'].curtain
                    # print('b', b)
                    current_pos_gate = b * the_plot['prev_pos_'+self.character]
                    current_pos_gate_sum = current_pos_gate.sum()
                    # print('current_pos_gate', current_pos_gate_sum)
                    prev_action_gate = (self.d * actions)
                    prev_action_gate_sum = prev_action_gate.sum()
                    # print('prev_action_gate', list(prev_action_gate))
                    reward += current_pos_gate_sum * prev_action_gate_sum
                    # print('calculated reward: ', reward)
            # Give ourselves a point for moving.
            the_plot.add_reward(reward)
        the_plot['prev_pos_'+self.character] = layers[self.character]

def make_game():
    game =  ascii_art_to_game(
      GAME_ART,
      what_lies_beneath=' ',
      drapes={'A': AgentDrape,
              '#': things.FixedDrape,
              # agent must be moving up to get bonus reward on up cell
              '^': Partial(DirectionalHoverRewardDrape, 
                dctns=torch.FloatTensor([0,0,CW_reward,CCW_reward,0])),
              # agent must be moving right to get bonus reward on right cell
              '>': Partial(DirectionalHoverRewardDrape, 
                dctns=torch.FloatTensor([CCW_reward,CW_reward,0,0,0])),
              # agent must be moving down to get bonus reward on down cell
              'v': Partial(DirectionalHoverRewardDrape, 
                dctns=torch.FloatTensor([0,0,CCW_reward,CW_reward,0])),
              # agent must be moving left to get bonus reward on left cell
              '<': Partial(DirectionalHoverRewardDrape, 
                dctns=torch.FloatTensor([CW_reward,CCW_reward,0,0,0])),
             },
      z_order='^>v<A#',
      update_schedule="A^>v<#")
    board, reward, discount = game.its_showtime()
    return game, board, reward, discount

def eval_cw_step(share_1, share_2, location_of_agent_pre, location_of_agent_post):
    """Evaluating a single clockwise step."""
    print('share_1', share_1, type(share_1))
    print('location_of_agent_pre', location_of_agent_pre, type(location_of_agent_pre))
    apa = share_1 * location_of_agent_pre
    apa = (apa[1] + apa[2] + apa[3]).sum()
    ba = share_2 * location_of_agent_post
    ba = (ba[1] + ba[2] + ba[3]).sum()
    return apa * ba


def eval_ccw_step(share_1, share_2, location_of_agent_pre, location_of_agent_post):
    """Evaluating a single counterclockwise step."""
    apa = share_2 * location_of_agent_pre
    apa = (apa[1] + apa[2] + apa[3]).sum()
    ba = share_1 * location_of_agent_post
    ba = (ba[1] + ba[2] + ba[3]).sum()
    return apa * ba


def step_perf(a, b, c, d, location_of_agent_pre, location_of_agent_post):
    # Evaluate for the clockwise step
    ab = eval_cw_step(a, b, location_of_agent_pre, location_of_agent_post)
    bc = eval_cw_step(b, c, location_of_agent_pre, location_of_agent_post)
    cd = eval_cw_step(c, d, location_of_agent_pre, location_of_agent_post)
    da = eval_cw_step(d, a, location_of_agent_pre, location_of_agent_post)
    cw = ab + bc + cd + da

    # Evaluate for counterclockwise step
    ab = eval_ccw_step(a, b, location_of_agent_pre, location_of_agent_post)
    bc = eval_ccw_step(b, c, location_of_agent_pre, location_of_agent_post)
    cd = eval_ccw_step(c, d, location_of_agent_pre, location_of_agent_post)
    da = eval_ccw_step(d, a, location_of_agent_pre, location_of_agent_post)
    ccw = ab + bc + cd + da
    return cw - ccw


def select_action_preset(t):
    """Deterministic actions for a preset optimal single loop for reward testing."""
    action = torch.zeros(5).float()
    if t < 2:
        # CW right
        action[1] = 1
    elif t >= 2 and t < 4:
        # Cw down
        action[3] = 1
    elif t >= 4 and t < 6:
        # CW left
        action[0] = 1
    elif t >= 6 and t < 8:
        # CW up
        action[2] = 1
    elif t >= 8 and t < 10:
        # CCW down
        action[3] = 1
    elif t >= 10 and t < 12:
        # CCW right
        action[1] = 1
    elif t >= 12 and t < 14:
        # CCW up 
        action[2] = 1
    elif t >= 14 and t < 17:
        # CCW left
        action[0] = 1
    else:
        # stay
        action[4] = 1
    return action