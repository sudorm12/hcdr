import logging
import numpy as np
import pandas as pd
from prepare_data import HCDRDataLoader
from sklearn.linear_model import LogisticRegression
from imblearn.over_sampling import RandomOverSampler
from sklearn.model_selection import KFold
from models import LinearNN, GBC, ABC, MultiLSTMWithMetadata
from grid_search import grid_search


def ensemble_fit_predict():
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

    loader_args = {
        'cc_tmax': 60,
        'bureau_tmax': 60,
        'pos_tmax': 60
    }

    loader = HCDRDataLoader(**loader_args)

    # load training and test data
    data_train, target_train = loader.load_train_data()
    data_val = loader.load_test_data()

    # oversample troubled loans to make up for imbalance
    ros = RandomOverSampler()
    os_index, target_train_os = ros.fit_sample(np.arange(data_train.shape[0]).reshape(-1, 1), target_train)
    data_train_os = data_train[os_index.squeeze()]

    # use predict on out of sample data and store results for each model
    num_models = 4
    train_samples = data_train[0].shape[0]
    test_samples = data_val[0].shape[0]
    train_results = np.empty((train_samples, num_models))
    val_results = np.empty((test_samples, num_models))

    # train on linear neural network
    linear_nn = LinearNN(data_train_os[0].shape[1], epochs=25)
    linear_nn.fit(data_train_os[0], target_train_os, data_val[0], target_val)

    train_results[:, 0] = linear_nn.predict(data_train[0]).squeeze()
    val_results[:, 0] = linear_nn.predict(data_val[0]).squeeze()

    # gradient boosting classifier
    gbc = GBC()
    gbc.fit(data_train_os[0], target_train_os)

    train_results[:, 1] = gbc.predict(data_train[0]).squeeze()
    val_results[:, 1] = gbc.predict(data_val[0]).squeeze()

    # adaboost classifier
    abc = ABC()
    abc.fit(data_train_os[0], target_train_os)

    train_results[:, 2] = abc.predict(data_train[0]).squeeze()
    val_results[:, 2] = abc.predict(data_val[0]).squeeze()

    model_args = {
        'epochs': 25,
        'batch_size': 512,
        'lstm_gpu': False,
        'sequence_dense_layers': 0,
        'sequence_dense_width': 8,
        'sequence_l2_reg': 0,
        'meta_dense_layers': 1,
        'meta_dense_width': 64,
        'meta_l2_reg': 1e-5,
        'meta_dropout': 0.2,
        'comb_dense_layers': 3,
        'comb_dense_width': 64,
        'comb_l2_reg': 1e-6,
        'comb_dropout': 0.2,
        'lstm_units': 8,
        'lstm_l2_reg': 1e-7
    }

    input_shape = loader.get_input_shape()
    lstm_nn = MultiLSTMWithMetadata(input_shape, **model_args)

    lstm_nn.fit(data_train_os, target_train_os, data_val, target_val)

    train_results[:, 3] = lstm_nn.predict(data_train).squeeze()
    val_results[:, 3] = lstm_nn.predict(data_val).squeeze()

    lr = LogisticRegression(class_weight='balanced')
    lr.fit(train_results, target_train.values)

    # TODO: collect SK_ID for out of sample data
    y = lr.predict(val_results)

    results = pd.DataFrame(np.concatenate([target_train, y.values.reshape(-1, 1)], axis=1))
    results.to_csv('data/results.csv')


def ensemble_fit_val():
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

    loader_args = {
        'cc_tmax': 60,
        'bureau_tmax': 60,
        'pos_tmax': 60
    }

    loader = HCDRDataLoader(**loader_args)
    app_ix = loader.get_index()

    kf = KFold(n_splits=4, shuffle=True)
    for fold_indexes in kf.split(app_ix):
        pass

    # load training and test data
    data_train_ts, target_train_ts, data_val_ts, target_val_ts = loader.load_train_val(fold_indexes[0], fold_indexes[1])
    input_shape = loader.get_input_shape()

    # oversample troubled loans to make up for imbalance
    ros = RandomOverSampler()
    os_index, target_train_os = ros.fit_sample(np.arange(data_train_ts[0].shape[0]).reshape(-1, 1), target_train_ts)
    data_train_ts_os = [data_train_part[os_index.squeeze()] for data_train_part in data_train_ts]
    target_train_ts_os = target_train_ts.values[os_index.squeeze()]

    # data_train_os = data_train[os_index.squeeze()]
    # use predict on out of sample data and store results for each model
    num_models = 4
    train_samples = target_train_ts.shape[0]
    val_samples = target_val_ts.shape[0]
    train_results = np.empty((train_samples, num_models))
    val_results = np.empty((val_samples, num_models))

    # train on linear neural network
    linear_nn = LinearNN(data_train_ts_os[0].shape[1], epochs=25)
    linear_nn.fit(data_train_ts_os[0], target_train_ts_os, data_val_ts[0], target_val_ts)

    train_results[:, 0] = linear_nn.predict(data_train_ts[0]).squeeze()
    val_results[:, 0] = linear_nn.predict(data_val_ts[0]).squeeze()

    # gradient boosting classifier
    gbc = GBC()
    gbc.fit(data_train_ts_os[0], target_train_os)

    train_results[:, 1] = gbc.predict(data_train_ts[0]).squeeze()
    val_results[:, 1] = gbc.predict(data_val_ts[0]).squeeze()

    # adaboost classifier
    abc = ABC()
    abc.fit(data_train_ts_os[0], target_train_os)

    train_results[:, 2] = abc.predict(data_train_ts[0]).squeeze()
    val_results[:, 2] = abc.predict(data_val_ts[0]).squeeze()

    model_args = {
        'epochs': 5,
        'batch_size': 256,
        'lstm_gpu': False,
        'sequence_dense_layers': 0,
        'sequence_dense_width': 8,
        'sequence_l2_reg': 0,
        'meta_dense_layers': 1,
        'meta_dense_width': 64,
        'meta_l2_reg': 1e-5,
        'meta_dropout': 0.2,
        'comb_dense_layers': 3,
        'comb_dense_width': 64,
        'comb_l2_reg': 1e-6,
        'comb_dropout': 0.2,
        'lstm_units': 8,
        'lstm_l2_reg': 1e-7
    }

    lstm_nn = MultiLSTMWithMetadata(input_shape, **model_args)

    lstm_nn.fit(data_train_ts_os, target_train_os, data_val_ts, target_val_ts)

    train_results[:, 3] = lstm_nn.predict(data_train_ts).squeeze()
    val_results[:, 3] = lstm_nn.predict(data_val_ts).squeeze()

    lr = LogisticRegression(class_weight='balanced')
    lr.fit(train_results, target_train_ts.values)

    # TODO: collect SK_ID for out of sample data
    y = lr.predict(val_results)

    results = pd.DataFrame(np.concatenate([val_results, y, target_val_ts.values.reshape(-1, 1)], axis=1))
    results.to_csv('data/results.csv')


def predict_test():
    pass


def hparam_grid_search():
    loader_args = {
        'cc_tmax': 60,
        'bureau_tmax': 60,
        'pos_tmax': 60
    }

    model_args = {
        'epochs': 25,
        'batch_size': 512,
        'lstm_gpu': True
    }

    grid_search(MultiLSTMWithMetadata, HCDRDataLoader,
                hp_file='grid_search_params.txt',
                loader_args=loader_args, model_args=model_args,
                random_oversample=True)


if __name__ == "__main__":
    ensemble_fit_val()
