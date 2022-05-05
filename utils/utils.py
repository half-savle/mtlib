import importlib
import logging
import datetime
import os
import sys
import numpy as np
import random
import torch
from mtlib.nn import *
from mtlib.Executor import *

class Data:
    def __init__(self, sources, destinations, timestamps, edge_idxs ):
        self.sources = sources
        self.destinations = destinations
        self.timestamps = timestamps
        self.edge_idxs = edge_idxs
        self.n_interactions = len(sources)
        self.unique_nodes = set(sources) | set(destinations)
        self.n_unique_nodes = len(self.unique_nodes)

def temporal_signal_split(dataset,train_ratio=0.7,valid_ratio=0.15):
    src_dst = dataset.edge_index
    time = dataset.timestamp
    idxs = src_dst[:,0]
    sources = src_dst[:,1]
    destinations = src_dst[:,2]
    full_data=Data(sources, destinations, time, idxs)
    val_time ,test_time=list(np.quantile(full_data.timestamps,[train_ratio , valid_ratio]))
    train_mask = time <= val_time
    test_time =time > test_time
    val_mask = np.logical_and(time <= test_time , time > val_time)

    train_data = Data(sources[train_mask], destinations[train_mask],
                      time[train_mask],edge_idxs[train_mask])

    val_data = Data(sources[val_mask], destinations[val_mask],
                    time[val_mask],edge_idxs[val_mask])

    test_data = Data(sources[test_mask], destinations[test_mask],
                     time[test_mask],edge_idxs[test_mask])

    return train_data,val_data,test_data


def get_executor(config, model, data_feature):
    """
    according the config['executor'] to create the executor

    Args:
        config(ConfigParser): config
        model(AbstractModel): model

    Returns:
        AbstractExecutor: the loaded executor
    """
    task=config['task']
    if task=="link_prediction":
        executor = Link_Prediction(config=config,model=model,data_feature=data_feature)
    if task=="node_classification":
        executor = Node_Classification(config=config, model=model, data_feature=data_feature)

def get_model(args, node_feature,edge_features):
    """
    according the config['model'] to create the model

    Args:
        config(ConfigParser): config
        data_feature(dict): feature of the data

    Returns:
        AbstractModel: the loaded model
    """
    model=config.get('model',None)
    assert model,"please check your input（no model）"
    if model=="CAW":
        load_model = CAWN(node_feature, edge_features, agg=args['agg'],
            num_layers=args['n_layer'], use_time=args['time'], attn_agg_method=args['attn_agg_method'], attn_mode=args['attn_mode'],
            n_head=args['attn_n_head'], drop_out=args['drop_out'], pos_dim=args['pos_dim'], pos_enc=args['pos_enc'],
            num_neighbors=args['n_degree'], walk_n_head=args['walk_n_head'], walk_mutual=args['walk_mutual'] if args['walk_pool'] == 'attn' else False ,walk_linear_out=args['walk_linear_out'], walk_pool=args['walk_pool'],
            cpu_cores=args['cpu_cores'], verbosity=args['verbosity'], get_checkpoint_path=args['saved_file'])
    return load_model

def get_evaluator(config):
    """
    according the config['evaluator'] to create the evaluator

    Args:
        config(ConfigParser): config

    Returns:
        AbstractEvaluator: the loaded evaluator
    """
    try:
        return getattr(importlib.import_module('libcity.evaluator'),
                       config['evaluator'])(config)
    except AttributeError:
        raise AttributeError('evaluator is not found')


def get_logger(config,save_dir=None ,name=None,):
    """
    获取Logger对象

    Args:
        config(ConfigParser): config
        name: specified name

    Returns:
        Logger: logger
    """
    if save_dir is None:
        log_dir = './libcity/log'
    else:
        log_dir = save_dir
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    log_filename = '{}-{}-{}-{}-{}.log'.format(config['exp_id'],
                                            config['model'], config['dataset'], config['task'],get_local_time())
    logfilepath = os.path.join(log_dir, log_filename)

    logger = logging.getLogger(name)

    log_level = config.get('log_level', 'INFO')

    if log_level.lower() == 'info':
        level = logging.INFO
    elif log_level.lower() == 'debug':
        level = logging.DEBUG
    elif log_level.lower() == 'error':
        level = logging.ERROR
    elif log_level.lower() == 'warning':
        level = logging.WARNING
    elif log_level.lower() == 'critical':
        level = logging.CRITICAL
    else:
        level = logging.INFO

    logger.setLevel(level)

    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler(logfilepath)
    file_handler.setFormatter(formatter)

    console_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s')
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(console_formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    logger.info('Log directory: %s', log_dir)
    return logger


def get_local_time():
    """
    获取时间

    Return:
        datetime: 时间
    """
    cur = datetime.datetime.now()
    cur = cur.strftime('%b-%d-%Y_%H-%M-%S')
    return cur


def ensure_dir(dir_path):
    """Make sure the directory exists, if it does not exist, create it.

    Args:
        dir_path (str): directory path
    """
    if not os.path.exists(dir_path):
        os.makedirs(dir_path)


def trans_naming_rule(origin, origin_rule, target_rule):
    """
    名字转换规则

    Args:
        origin (str): 源命名格式下的变量名
        origin_rule (str): 源命名格式，枚举类
        target_rule (str): 目标命名格式，枚举类

    Return:
        target (str): 转换之后的结果
    """
    # TODO: 请确保输入是符合 origin_rule，这里目前不做检查
    target = ''
    if origin_rule == 'upper_camel_case' and target_rule == 'under_score_rule':
        for i, c in enumerate(origin):
            if i == 0:
                target = c.lower()
            else:
                target += '_' + c.lower() if c.isupper() else c
        return target
    else:
        raise NotImplementedError(
            'trans naming rule only support from upper_camel_case to \
                under_score_rule')


def preprocess_data(data, config):
    """
    split by input_window and output_window

    Args:
        data: shape (T, ...)

    Returns:
        np.ndarray: (train_size/test_size, input_window, ...)
                    (train_size/test_size, output_window, ...)

    """
    train_rate = config.get('train_rate', 0.7)
    eval_rate = config.get('eval_rate', 0.1)

    input_window = config.get('input_window', 12)
    output_window = config.get('output_window', 3)

    x, y = [], []
    for i in range(len(data) - input_window - output_window):
        a = data[i: i + input_window + output_window]  # (in+out, ...)
        x.append(a[0: input_window])  # (in, ...)
        y.append(a[input_window: input_window + output_window])  # (out, ...)
    x = np.array(x)  # (num_samples, in, ...)
    y = np.array(y)  # (num_samples, out, ...)

    train_size = int(x.shape[0] * (train_rate + eval_rate))
    trainx = x[:train_size]  # (train_size, in, ...)
    trainy = y[:train_size]  # (train_size, out, ...)
    testx = x[train_size:x.shape[0]]  # (test_size, in, ...)
    testy = y[train_size:x.shape[0]]  # (test_size, out, ...)
    return trainx, trainy, testx, testy


def set_random_seed(seed):
    """
    重置随机数种子

    Args:
        seed(int): 种子数
    """
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
