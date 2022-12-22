from agents.PPO import *
from torch.distributions import Normal


class RPG(PPO):
  '''
  Implementation of RPG (Reward Policy Gradient)
  '''
  def __init__(self, cfg):
    super().__init__(cfg)
    # Set optimizer for reward function
    self.optimizer['reward'] = getattr(torch.optim, cfg['optimizer']['name'])(self.network.reward_params, **cfg['optimizer']['reward_kwargs'])
    # Set replay buffer
    self.replay = FiniteReplay(self.cfg['steps_per_epoch']+1, keys=['state', 'action', 'reward', 'mask', 'v', 'log_pi', 'ret', 'adv', 'ppo_adv'])
    # Set state normalizer
    self.state_normalizer = MeanStdNormalizer()

  def createNN(self, input_type):
    # Set feature network
    if input_type == 'pixel':
      input_size = self.cfg['feature_dim']
      if 'MinAtar' in self.env_name:
        feature_net = Conv2d_MinAtar(in_channels=self.env[mode].game.state_shape()[2], feature_dim=input_size)
      else:
        feature_net = Conv2d_Atari(in_channels=4, feature_dim=input_size)
    elif input_type == 'feature':
      input_size = self.state_size
      feature_net = nn.Identity()
    # Set actor network
    assert self.action_type == 'CONTINUOUS', f"{self.cfg['agent']['name']} only supports continous action spaces."
    actor_net = MLPGaussianActor(action_lim=self.action_lim, layer_dims=[input_size]+self.cfg['hidden_layers']+[self.action_size], hidden_act=self.cfg['hidden_act'], rsample=False)
    # Set critic network (state value)
    critic_net = MLPCritic(layer_dims=[input_size]+self.cfg['hidden_layers']+[1], hidden_act=self.cfg['hidden_act'], output_act=self.cfg['output_act'])
    # Set reward network
    reward_net = MLPQCritic(layer_dims=[input_size+self.action_size]+self.cfg['hidden_layers']+[1], hidden_act=self.cfg['hidden_act'], output_act=self.cfg['output_act'])
    # Set the model
    NN = ActorVCriticRewardNet(feature_net, actor_net, critic_net, reward_net)
    return NN

  def learn(self):
    mode = 'Train'
    # Compute return and advantage
    adv = torch.tensor(0.0)
    ret = self.replay.v[-1].detach()
    self.replay.adv[-2] = ret
    for i in reversed(range(self.cfg['steps_per_epoch'])):
      ret = self.replay.reward[i] + self.discount * self.replay.mask[i] * ret
      self.replay.ret[i] = ret.detach()
      if self.cfg['gae'] < 0:
        adv = ret - self.replay.v[i].detach()
      else:
        td_error = self.replay.reward[i] + self.discount * self.replay.mask[i] * self.replay.v[i+1] - self.replay.v[i]
        adv = self.discount * self.cfg['gae'] * self.replay.mask[i] * adv + td_error
      self.replay.ppo_adv[i] = adv.detach()
      if i >= 1: # use lambda return as the advantage
        self.replay.adv[i-1] = (adv + self.replay.v[i]).detach()
    # Get training data and detach
    entries = self.replay.get(['log_pi', 'ret', 'adv', 'state', 'action', 'reward', 'v', 'mask', 'ppo_adv'], self.cfg['steps_per_epoch'], detach=True)
    # Compute advantage
    entries.ppo_adv.copy_((entries.ppo_adv - entries.ppo_adv.mean()) / entries.ppo_adv.std())
    entries.adv.copy_(self.discount * entries.adv - entries.v)
    # Optimize for multiple epochs
    for _ in range(self.cfg['optimize_epochs']):
      batch_idxs = generate_batch_idxs(len(entries.log_pi), self.cfg['batch_size'])
      for batch_idx in batch_idxs:
        batch_idx = to_tensor(batch_idx, self.device).long()
        prediction = self.network(entries.state[batch_idx], entries.action[batch_idx])
        # Take an optimization step for actor
        approx_kl = (entries.log_pi[batch_idx] - prediction['log_pi']).mean()
        if approx_kl <= 1.5 * self.cfg['target_kl']:
          # Freeze reward network to avoid computing gradients for it
          for p in self.network.reward_net.parameters():
            p.requires_grad = False
          # Get predicted reward
          repara_action = self.network.get_repara_action(entries.state[batch_idx], entries.action[batch_idx])
          predicted_reward = self.network.get_reward(entries.state[batch_idx], repara_action)
          # Compute clipped objective
          ratio = torch.exp(prediction['log_pi'] - entries.log_pi[batch_idx]).detach()
          obj = predicted_reward + entries.adv[batch_idx] * prediction['log_pi']
          mask = (entries.ppo_adv[batch_idx]>0) & (ratio > 1+self.cfg['clip_ratio'])
          mask = mask | ((entries.ppo_adv[batch_idx]<0) & (ratio < 1-self.cfg['clip_ratio']))
          ratio[mask] = 0.0
          actor_loss = -(ratio*obj).mean()
          self.optimizer['actor'].zero_grad()
          actor_loss.backward()
          if self.gradient_clip > 0:
            nn.utils.clip_grad_norm_(self.network.actor_params, self.gradient_clip)
          self.optimizer['actor'].step()
          # Unfreeze reward network
          for p in self.network.reward_net.parameters():
            p.requires_grad = True
        # Take an optimization step for critic
        critic_loss = (entries.ret[batch_idx] - prediction['v']).pow(2).mean()
        self.optimizer['critic'].zero_grad()
        critic_loss.backward()
        if self.gradient_clip > 0:
          nn.utils.clip_grad_norm_(self.network.critic_params, self.gradient_clip)
        self.optimizer['critic'].step()
        # Take an optimization step for reward
        predicted_reward = self.network.get_reward(entries.state[batch_idx], entries.action[batch_idx])
        reward_loss = (predicted_reward - entries.reward[batch_idx]).pow(2).mean()
        self.optimizer['reward'].zero_grad()
        reward_loss.backward()
        if self.gradient_clip > 0:
          nn.utils.clip_grad_norm_(self.network.reward_params, self.gradient_clip)
        self.optimizer['reward'].step()
    # Log
    if self.show_tb:
      try:
        self.logger.add_scalar('actor_loss', actor_loss.item(), self.step_count)
      except:
        pass
      self.logger.add_scalar('critic_loss', critic_loss.item(), self.step_count)
      self.logger.add_scalar('reward_loss', reward_loss.item(), self.step_count)