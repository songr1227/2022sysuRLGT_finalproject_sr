import math
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.distributions import Categorical, Normal
# torch.autograd.set_detect_anomaly(True)


activations = {
  'Linear': nn.Identity(),
  'ReLU': nn.ReLU(),
  'ELU': nn.ELU(),
  'Softplus': nn.Softplus(),
  'LeakyReLU': nn.LeakyReLU(),
  'Tanh': nn.Tanh(),
  'Sigmoid': nn.Sigmoid(),
  'Hardsigmoid': nn.Hardsigmoid(),
  'Softmax-1': nn.Softmax(dim=-1),
  'Softmax0': nn.Softmax(dim=0),
  'Softmax1': nn.Softmax(dim=1),
  'Softmax2': nn.Softmax(dim=2)
}


def layer_init(layer, init_type='default', nonlinearity='relu', w_scale=1.0):
  nonlinearity = nonlinearity.lower()
  # Initialize all weights and biases in layer and return it
  if init_type in ['uniform_', 'normal_']:
    getattr(nn.init, init_type)(layer.weight.data)
  elif init_type in ['xavier_uniform_', 'xavier_normal_', 'orthogonal_']:
    # Compute the recommended gain value for the given nonlinearity
    gain = nn.init.calculate_gain(nonlinearity)
    getattr(nn.init, init_type)(layer.weight.data, gain=gain)
  elif init_type in ['kaiming_uniform_', 'kaiming_normal_']:
    getattr(nn.init, init_type)(layer.weight.data, mode='fan_in', nonlinearity=nonlinearity)
  else: # init_type == 'default'
    return layer
  layer.weight.data.mul_(w_scale)
  nn.init.zeros_(layer.bias.data)
  return layer


# Adapted from https://github.com/Kaixhin/Rainbow/blob/master/model.py
class NoisyLinear(nn.Module):
  '''
  Noisy linear layer with Factorised Gaussian noise
  '''
  def __init__(self, in_features, out_features, std_init=0.4):
    super().__init__()
    self.in_features = in_features
    self.out_features = out_features
    self.std_init = std_init
    self.weight_mu = nn.Parameter(torch.Tensor(out_features, in_features))
    self.weight_sigma = nn.Parameter(torch.Tensor(out_features, in_features))
    self.bias_mu = nn.Parameter(torch.Tensor(out_features))
    self.bias_sigma = nn.Parameter(torch.Tensor(out_features))
    self.register_buffer('weight_epsilon', torch.Tensor(out_features, in_features))
    self.register_buffer('bias_epsilon', torch.Tensor(out_features))
    self.reset_parameters()
    self.reset_noise()

  def reset_parameters(self):
    mu_range = 1 / math.sqrt(self.in_features)
    self.weight_mu.data.uniform_(-mu_range, mu_range)
    self.weight_sigma.data.fill_(self.std_init / math.sqrt(self.in_features))
    self.bias_mu.data.uniform_(-mu_range, mu_range)
    self.bias_sigma.data.fill_(self.std_init / math.sqrt(self.out_features))

  def _scale_noise(self, size):
    x = torch.randn(size)
    return x.sign().mul_(x.abs().sqrt_())

  def reset_noise(self):
    epsilon_in = self._scale_noise(self.in_features)
    epsilon_out = self._scale_noise(self.out_features)
    self.weight_epsilon.copy_(epsilon_out.outer(epsilon_in))
    self.bias_epsilon.copy_(epsilon_out)

  def forward(self, input):
    if self.training:
      return F.linear(input, self.weight_mu + self.weight_sigma * self.weight_epsilon, self.bias_mu + self.bias_sigma * self.bias_epsilon)
    else:
      return F.linear(input, self.weight_mu, self.bias_mu)


class MLP(nn.Module):
  '''
  Multilayer Perceptron
  '''
  def __init__(self, layer_dims, hidden_act='ReLU', output_act='Linear', init_type='kaiming_uniform_', w_scale=1.0, last_w_scale=1.0):
    super().__init__()
    # Create layers
    layers = []
    for i in range(len(layer_dims)-1):
      act = hidden_act if i+2 != len(layer_dims) else output_act
      w_s = w_scale if i+2 != len(layer_dims) else last_w_scale
      layers.append(
        layer_init(
          nn.Linear(layer_dims[i], layer_dims[i+1], bias=True), 
          init_type=init_type, 
          nonlinearity=act,
          w_scale=w_s
        )
      )
      layers.append(activations[act])
    self.mlp = nn.Sequential(*layers) 
  
  def forward(self, x):
    return self.mlp(x)


class NoisyMLP(nn.Module):
  '''
  Multilayer Perceptron with Noisy nets
  '''
  def __init__(self, layer_dims, hidden_act='ReLU', output_act='Linear'):
    super().__init__()
    # Create layers
    layers = []
    for i in range(len(layer_dims)-1):
      act = hidden_act if i+2 != len(layer_dims) else output_act
      layers.append(NoisyLinear(layer_dims[i], layer_dims[i+1]))
      layers.append(activations[act])
    self.mlp = nn.Sequential(*layers) 
  
  def forward(self, x):
    return self.mlp(x)
  
  def reset_noise(self):
    for layer in self.mlp:
      if isinstance(layer, NoisyLinear):
        layer.reset_noise()


class Conv2dLayers(nn.Module):
  '''
  Multiple Conv2d layers
  '''
  def __init__(self, layer_dims, hidden_act='ReLU', output_act='Linear'):
    super().__init__()
    # Create layers
    layers = []
    for i in range(len(layer_dims)-1):
      layers.append(
        layer_init(
          nn.Conv2d(layer_dims[i], layer_dims[i+1], kernel_size=len(layer_dims)-i, stride=1),
          nonlinearity=hidden_act
        )
      )
      layers.append(activations[hidden_act])
    layers.append(
      layer_init(
        nn.Conv2d(layer_dims[-1], layer_dims[-1], kernel_size=1, stride=1),
        nonlinearity=output_act
      )
    )
    layers.append(activations[output_act])
    self.conv = nn.Sequential(*layers)
  
  def forward(self, x):
    return self.conv(x)


class ConvTranspose2dLayers(nn.Module):
  '''
  Multiple ConvTranspose2d layers
  '''
  def __init__(self, layer_dims, hidden_act='ReLU', output_act='Sigmoid'):
    super().__init__()
    # Create layers
    layers = []
    layers.append(
      layer_init(
        nn.ConvTranspose2d(layer_dims[0], layer_dims[0], kernel_size=1, stride=1),
        nonlinearity=hidden_act
      )
    )
    for i in range(len(layer_dims)-1):
      layers.append(activations[hidden_act])
      layers.append(
        layer_init(
          nn.ConvTranspose2d(layer_dims[i], layer_dims[i+1], kernel_size=i+2, stride=1),
          nonlinearity=hidden_act
        )
      )
    layers.append(activations[output_act])
    self.conv = nn.Sequential(*layers)
  
  def forward(self, x):
    return self.conv(x)


class Conv2d_Atari(nn.Module):
  '''
  2D convolution neural network for Atari games
  '''
  def __init__(self, in_channels=4, feature_dim=512):
    super().__init__()
    self.conv1 = layer_init(nn.Conv2d(in_channels, 32, kernel_size=8, stride=4))
    self.conv2 = layer_init(nn.Conv2d(32, 64, kernel_size=4, stride=2))
    self.conv3 = layer_init(nn.Conv2d(64, 64, kernel_size=3, stride=1))
    linear_input_size = 7 * 7 * 64
    self.fc4 = layer_init(nn.Linear(linear_input_size, feature_dim))

  def forward(self, x):
    y = F.relu(self.conv1(x))
    y = F.relu(self.conv2(y))
    y = F.relu(self.conv3(y))
    y = y.view(y.size(0), -1)
    y = F.relu(self.fc4(y))
    return y


class Conv2d_MinAtar(nn.Module):
  '''
  2D convolution neural network for MinAtar games
  '''
  def __init__(self, in_channels, feature_dim=128):
    super().__init__()
    self.conv1 = layer_init(nn.Conv2d(in_channels, 16, kernel_size=3, stride=1))
    def size_linear_unit(size, kernel_size=3, stride=1):
      return (size - (kernel_size - 1) - 1) // stride + 1
    linear_input_size = size_linear_unit(10) * size_linear_unit(10) * 16
    self.fc2 = layer_init(nn.Linear(linear_input_size, feature_dim))
    
  def forward(self, x):
    y = F.relu(self.conv1(x))
    y = y.view(y.size(0), -1)
    y = F.relu(self.fc2(y))
    return y


class NetworkGlue(nn.Module):
  '''
  Glue two networks
  '''
  def __init__(self, net1, net2):
    super().__init__()
    self.net1 = net1
    self.net2 = net2

  def forward(self, x):
    y = self.net2(self.net1(x))
    return y


class DQNNet(nn.Module):
  def __init__(self, feature_net, value_net):
    super().__init__()
    self.feature_net = feature_net
    self.value_net = value_net

  def forward(self, obs):
    # Generate the latent feature
    phi = self.feature_net(obs)
    # Compute action values for all actions
    q = self.value_net(phi)
    return q


class BootstrappedDQNNet(nn.Module):
  def __init__(self, feature_net, heads_net):
    super().__init__()
    self.feature_net = feature_net
    self.heads_net = heads_net
    self.k = len(heads_net)

  def forward(self, obs, head_idx='all'):
    # Generate the latent feature
    phi = self.feature_net(obs)
    # Compute action values for all actions
    if head_idx == 'all':
      all_q = [head(phi) for head in self.heads_net]
      return all_q
    else:
      assert head_idx >= 0 and head_idx < self.k, 'Wrong head index!'
      q = self.heads_net[head_idx](phi)
      return q


class MLPCritic(nn.Module):
  def __init__(self, layer_dims, hidden_act='ReLU', output_act='Linear', last_w_scale=1e-3):
    super().__init__()
    self.value_net = MLP(layer_dims=layer_dims, hidden_act=hidden_act, output_act=output_act, last_w_scale=last_w_scale)

  def forward(self, phi):
    return self.value_net(phi).squeeze(-1)


class NoisyMLPCritic(nn.Module):
  def __init__(self, layer_dims, hidden_act='ReLU', output_act='Linear'):
    super().__init__()
    self.value_net = NoisyMLP(layer_dims=layer_dims, hidden_act=hidden_act, output_act=output_act)

  def forward(self, phi):
    return self.value_net(phi).squeeze(-1)
  
  def reset_noise(self):
    self.value_net.reset_noise()


class MLPQCritic(nn.Module):
  def __init__(self, layer_dims, hidden_act='ReLU', output_act='Linear', last_w_scale=1e-3):
    super().__init__()
    self.Q = MLP(layer_dims=layer_dims, hidden_act=hidden_act, output_act=output_act, last_w_scale=last_w_scale)

  def forward(self, phi, action):
    phi_action = torch.cat([phi, action], dim=-1)
    q = self.Q(phi_action).squeeze(-1)
    return q


class MLPDoubleQCritic(nn.Module):
  def __init__(self, layer_dims, hidden_act='ReLU', output_act='Linear', last_w_scale=1e-3):
    super().__init__()
    self.Q1 = MLP(layer_dims=layer_dims, hidden_act=hidden_act, output_act=output_act, last_w_scale=last_w_scale)
    self.Q2 = MLP(layer_dims=layer_dims, hidden_act=hidden_act, output_act=output_act, last_w_scale=last_w_scale)

  def forward(self, phi, action):
    phi_action = torch.cat([phi, action], dim=-1)
    q1 = self.Q1(phi_action).squeeze(-1)
    q2 = self.Q2(phi_action).squeeze(-1)
    return q1, q2


class MLPCategoricalActor(nn.Module):
  def __init__(self, layer_dims, hidden_act='ReLU', output_act='Linear', last_w_scale=1e-3):
    super().__init__()
    self.logits_net = MLP(layer_dims=layer_dims, hidden_act=hidden_act, output_act=output_act, last_w_scale=last_w_scale)

  def distribution(self, phi):
    logits = self.logits_net(phi)
    return Categorical(logits=logits)

  def log_pi_from_distribution(self, action_distribution, action):
    return action_distribution.log_prob(action)

  def forward(self, phi, action=None, deterministic=False):
    # Compute action distribution and the log_pi of given actions
    action_distribution = self.distribution(phi)
    if action is None:
      action = action_distribution.sample()
    log_pi = self.log_pi_from_distribution(action_distribution, action)
    return action, None, None, log_pi


class MLPGaussianActor(nn.Module):
  def __init__(self, action_lim, layer_dims, hidden_act='ReLU', last_w_scale=1e-3, rsample=False):
    super().__init__()
    self.rsample = rsample
    self.actor_net = MLP(layer_dims=layer_dims, hidden_act=hidden_act, output_act='Tanh', last_w_scale=last_w_scale)
    # The action std is independent of states
    self.action_std = nn.Parameter(torch.zeros(layer_dims[-1]))
    self.action_lim = action_lim

  def distribution(self, phi):
    action_mean = self.action_lim * self.actor_net(phi)
    # Constrain action_std inside [1e-6, 10]
    action_std = torch.clamp(F.softplus(self.action_std), 1e-6, 10)
    return action_mean, action_std, Normal(action_mean, action_std)
    
  def log_pi_from_distribution(self, action_distribution, action):
    # Last axis sum needed for Torch Normal distribution
    log_pi = action_distribution.log_prob(action).sum(axis=-1)
    # Constrain log_pi inside [-20, 20]
    log_pi = torch.clamp(log_pi, -20, 20)
    return log_pi

  def forward(self, phi, action=None, deterministic=False):
    # Compute action distribution and the log_pi of given actions
    action_mean, action_std, action_distribution = self.distribution(phi)
    if action is None:
      if deterministic:
        action = action_mean
      else:
        action = action_distribution.rsample() if self.rsample else action_distribution.sample()
    log_pi = self.log_pi_from_distribution(action_distribution, action)
    return action, action_mean, action_std, log_pi


class MLPSquashedGaussianActor(nn.Module):
  def __init__(self, action_lim, layer_dims, hidden_act='ReLU', last_w_scale=1e-3, rsample=False):
    super().__init__()
    self.rsample = rsample
    self.actor_net = MLP(layer_dims=layer_dims, hidden_act=hidden_act, output_act='Linear', last_w_scale=last_w_scale)
    self.action_lim = action_lim

  def distribution(self, phi):
    action_mean, action_std = self.actor_net(phi).chunk(2, dim=-1)
    # Constrain action_std inside [1e-6, 10]
    action_std = torch.clamp(F.softplus(action_std), 1e-6, 10)
    return action_mean, action_std, Normal(action_mean, action_std)

  def log_pi_from_distribution(self, action_distribution, action):
    # NOTE: Check out the original SAC paper and https://github.com/openai/spinningup/issues/279 for details
    log_pi = action_distribution.log_prob(action).sum(axis=-1)
    log_pi -= (2*(math.log(2) - action - F.softplus(-2*action))).sum(axis=-1)
    # Constrain log_pi inside [-20, 20]
    log_pi = torch.clamp(log_pi, -20, 20)
    return log_pi

  def forward(self, phi, action=None, deterministic=False):
    # Compute action distribution and the log_pi of given actions
    action_mean, action_std, action_distribution = self.distribution(phi)
    if action is None:
      if deterministic:
        u = action_mean
      else:
        u = action_distribution.rsample() if self.rsample else action_distribution.sample()
      action = self.action_lim * torch.tanh(u)
    else:
      u = torch.clamp(action / self.action_lim, -0.999, 0.999)
      u = torch.atanh(u)
    # Compute logprob from Gaussian, and then apply correction for Tanh squashing.
    log_pi = self.log_pi_from_distribution(action_distribution, u)
    return action, action_mean, action_std, log_pi


class MLPDeterministicActor(nn.Module):
  def __init__(self, action_lim, layer_dims, hidden_act='ReLU', last_w_scale=1e-3):
    super().__init__()
    self.actor_net = MLP(layer_dims=layer_dims, hidden_act=hidden_act, output_act='Tanh', last_w_scale=last_w_scale)
    self.action_lim = action_lim
  
  def forward(self, phi, action=None, deterministic=False):
    return self.action_lim * self.actor_net(phi), None, None, None


class MLPStdGaussianActor(MLPSquashedGaussianActor):
  def __init__(self, action_lim, layer_dims, hidden_act='ReLU', last_w_scale=1e-3, rsample=False):
    super().__init__(action_lim, layer_dims, hidden_act, last_w_scale, rsample)

  def distribution(self, phi):
    action_mean, action_std = self.actor_net(phi).chunk(2, dim=-1)
    action_mean = self.action_lim * torch.tanh(action_mean)
    # Constrain action_std inside [1e-6, 10]
    action_std = torch.clamp(F.softplus(action_std), 1e-6, 10)
    return action_mean, action_std, Normal(action_mean, action_std)

  def log_pi_from_distribution(self, action_distribution, action):
    # Last axis sum needed for Torch Normal distribution
    log_pi = action_distribution.log_prob(action).sum(axis=-1)
    # Constrain log_pi inside [-20, 20]
    log_pi = torch.clamp(log_pi, -20, 20)
    return log_pi

  def forward(self, phi, action=None, deterministic=False):
    # Compute action distribution and the log_pi of given actions
    action_mean, action_std, action_distribution = self.distribution(phi)
    if action is None:
      if deterministic:
        action = action_mean
      else:
        action = action_distribution.rsample() if self.rsample else action_distribution.sample()
    log_pi = self.log_pi_from_distribution(action_distribution, action)
    return action, action_mean, action_std, log_pi


class REINFORCENet(nn.Module):
  def __init__(self, feature_net, actor_net):
    super().__init__()
    self.feature_net = feature_net
    self.actor_net = actor_net
    self.actor_params = list(self.feature_net.parameters()) + list(self.actor_net.parameters())

  def forward(self, obs, action=None, deterministic=False):
    # Generate the latent feature
    phi = self.feature_net(obs)
    # Sample an action
    action, _, _, log_pi = self.actor_net(phi, action, deterministic)
    return {'action': action, 'log_pi': log_pi}


class ActorVCriticNet(nn.Module):
  def __init__(self, feature_net, actor_net, critic_net):
    super().__init__()
    self.feature_net = feature_net
    self.actor_net = actor_net
    self.critic_net = critic_net
    self.actor_params = list(self.feature_net.parameters()) + list(self.actor_net.parameters())
    self.critic_params = list(self.feature_net.parameters()) + list(self.critic_net.parameters())

  def forward(self, obs, action=None, deterministic=False):
    # Generate the latent feature
    phi = self.feature_net(obs)
    # Compute state value
    v = self.critic_net(phi)
    # Sample an action
    action, _, _, log_pi = self.actor_net(phi, action, deterministic)
    return {'action': action, 'log_pi': log_pi, 'v': v}
  
  def get_log_pi(self, obs, action):
    # Generate the latent feature
    phi = self.feature_net(obs)
    _, _, _, log_pi = self.actor_net(phi, action=action)
    return log_pi
  
  def get_state_value(self, obs):
    # Generate the latent feature
    phi = self.feature_net(obs)
    # Compute state value
    v = self.critic_net(phi)
    return v
  
  def get_entropy_pi(self, obs):
    # Generate the latent feature
    phi = self.feature_net(obs)
    # Get the distribution of pi
    _, action_std, dist = self.actor_net.distribution(phi)
    return action_std, dist.entropy().sum(-1)


class ActorQCriticNet(ActorVCriticNet):
  def __init__(self, feature_net, actor_net, critic_net):
    super().__init__(feature_net, actor_net, critic_net)

  def forward(self, obs, action=None, deterministic=False):
    # Generate the latent feature
    phi = self.feature_net(obs)
    # Sample an action
    action, _, _, _ = self.actor_net(phi, action, deterministic)
    # Compute state-action value
    q = self.critic_net(phi, action)
    return {'action': action, 'q': q}

  def get_q(self, obs, action):
    # Generate the latent feature
    phi = self.feature_net(obs)
    # Compute state-action value
    q = self.critic_net(phi, action)
    return q


class ActorDoubleQCriticNet(ActorQCriticNet):
  def __init__(self, feature_net, actor_net, critic_net):
    super().__init__(feature_net, actor_net, critic_net)

  def forward(self, obs, action=None, deterministic=False):
    # Generate the latent feature
    phi = self.feature_net(obs)
    # Sample an action
    action, _, _, log_pi = self.actor_net(phi, action, deterministic)
    # Compute state-action value
    q1, q2 = self.critic_net(phi, action)
    return {'action': action, 'log_pi': log_pi, 'q1': q1, 'q2': q2}
  
  def get_q(self, obs, action):
    # Generate the latent feature
    phi = self.feature_net(obs)
    # Compute state-action value
    q1, q2 = self.critic_net(phi, action)
    return q1, q2


class ActorVCriticRewardNet(ActorVCriticNet):
  def __init__(self, feature_net, actor_net, critic_net, reward_net):
    super().__init__(feature_net, actor_net, critic_net)
    self.reward_net = reward_net
    self.reward_params = list(self.feature_net.parameters()) + list(self.reward_net.parameters())

  def forward(self, obs, action=None, deterministic=False):
    # Generate the latent feature
    phi = self.feature_net(obs)
    # Compute state value
    v = self.critic_net(phi)
    # Sample an action
    action, _, _, log_pi = self.actor_net(phi, action, deterministic)
    return {'action': action, 'log_pi': log_pi, 'v': v}

  def get_reward(self, obs, action):
    # Generate the latent feature
    phi = self.feature_net(obs)
    # Compute predicted reward
    reward = self.reward_net(phi, action)
    return reward
  
  def get_repara_action(self, obs, action):
    # Generate the latent feature
    phi = self.feature_net(obs)
    # Reparameterize action with epsilon
    action_mean, action_std, _ = self.actor_net.distribution(phi)
    if isinstance(self.actor_net, MLPSquashedGaussianActor):
      action = torch.clamp(action / self.actor_net.action_lim, -0.999, 0.999)
      u = torch.atanh(action)
      eps = (u - action_mean) / action_std
      repara_action = self.actor_net.action_lim * torch.tanh(action_mean + action_std * eps.detach())
    else:
      eps = (action - action_mean) / action_std
      repara_action = action_mean + action_std * eps.detach()
    return repara_action