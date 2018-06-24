import pandas as pd
import numpy as np
import logging
from sklearn.preprocessing import StandardScaler
from soft_impute import SoftImpute
from sklearn.decomposition import PCA
from sklearn.linear_model import LinearRegression


class HCDALoader:
    def __init__(self, data_dir='data'):
        self._data_dir = data_dir
        self._curr_home_imputer = SoftImpute()
        self._amt_gp_lr = LinearRegression()
        self._amt_an_lr = LinearRegression()
        self._st_pca = None
        self._num_scaler = StandardScaler()
        self._mean_imp_cols = None
        self._mean_imp_means = None

        logging.debug('Reading application_train.csv...')
        self._applications = pd.read_csv('{}/application_train.csv'.format(data_dir), index_col="SK_ID_CURR")
        logging.debug('Finished reading application_train.csv')

        self._bureau_summary = self.read_bureau()
        self._previous_summary = self.read_previous_application()

    def load_train_val(self, train_index, val_index):
        # load each of the available data tables
        applications_train = self.read_applications(train_index)
        applications_val = self.read_applications(val_index, fit_transform=False)
        
        # join the dataframes together and fill nas with zeros
        # TODO: add debug messages
        joined_train = applications_train.join(self._bureau_summary, rsuffix='_BUREAU').join(self._previous_summary,
                                                                                             rsuffix='_PREVIOUS')
        full_data_train = joined_train.combine_first(joined_train.select_dtypes(include=[np.number]).fillna(0))

        joined_val = applications_val.join(self._bureau_summary, rsuffix='_BUREAU').join(self._previous_summary,
                                                                                         rsuffix='_PREVIOUS')
        full_data_val = joined_val.combine_first(joined_val.select_dtypes(include=[np.number]).fillna(0))

        # split full data into features and target
        data_train = full_data_train.drop('TARGET', axis=1)
        target_train = full_data_train['TARGET']
        data_val = full_data_val.drop('TARGET', axis=1)
        target_val = full_data_val['TARGET']

        # scale numeric columns before returning
        data_train = self._num_scaler.fit_transform(data_train.loc[:, data_train.dtypes == np.number])
        data_val = self._num_scaler.transform(data_val.loc[:, data_val.dtypes == np.number])

        return data_train, target_train, data_val, target_val

    def read_applications(self, split_index=None, fit_transform=True):
        if split_index is None:
            apps_clean = self._applications.copy()
        else:
            apps_clean = self._applications.iloc[split_index].copy()

        # TODO: add debug messages throughout method

        # track rows with high number of na values
        apps_clean['NA_COLS'] = apps_clean.isna().sum(axis=1)

        # change y/n columns to boolean
        yn_cols = ['FLAG_OWN_CAR', 
                   'FLAG_OWN_REALTY']

        apps_clean[yn_cols] = self._yn_cols_to_boolean(apps_clean, yn_cols)

        # identify encoded columns, fill na with unspecified and change to categorical
        apps_clean = self._cat_data_dummies(apps_clean)

        # use smart imputation and pca on current home information
        stat_suffixes = ['_AVG', '_MEDI', '_MODE']
        stat_cols = [col for col in apps_clean.columns[apps_clean.columns.str.contains('|'.join(stat_suffixes))]]

        X = apps_clean[stat_cols].values
        if True:  # fit_transform:
            # TODO: only fit for fit_transform
            self._curr_home_imputer.fit(X)

        apps_clean[stat_cols] = self._curr_home_imputer.predict(X)
        full_home_stats = apps_clean[stat_cols]

        # TODO: may want to set a selectable threshold for PCA columns based on explained variance
        pca_components = 15
        pca_cols = ['CURR_HOME_' + str(pca_col) for pca_col in range(pca_components)]

        if fit_transform:
            self._st_pca = PCA(n_components=pca_components)
            self._st_pca.fit(full_home_stats)

        home_stats_pca = pd.DataFrame(self._st_pca.transform(full_home_stats),
                                      index=full_home_stats.index,
                                      columns=pca_cols)

        apps_clean = apps_clean.join(home_stats_pca)
        apps_clean = apps_clean.drop(stat_cols, axis=1)

        # age of car if owned
        # TODO: impute car age with average, if client owns a car

        # impute all credit bureau requests with zero, except past year with one
        app_credit_cols = apps_clean.columns[apps_clean.columns.str.contains('AMT_REQ_CREDIT_BUREAU')]
        apps_clean['AMT_REQ_CREDIT_BUREAU_YEAR'] = apps_clean['AMT_REQ_CREDIT_BUREAU_YEAR'].fillna(1)
        apps_clean[app_credit_cols] = apps_clean[app_credit_cols].fillna(0)

        # fit linear regression to infer goods price and annuity amount from credit using linear regression
        if fit_transform:
            amt_lr_rows = apps_clean['AMT_GOODS_PRICE'].notna()
            x = apps_clean.loc[amt_lr_rows]['AMT_CREDIT']
            y = apps_clean.loc[amt_lr_rows]['AMT_GOODS_PRICE']
            self._amt_gp_lr.fit(x.values.reshape(-1, 1), y)

        # use fitted linear regression to predict goods price
        amt_fill_rows = apps_clean['AMT_GOODS_PRICE'].isna()
        x = apps_clean.loc[amt_fill_rows]['AMT_CREDIT']
        y = self._amt_gp_lr.predict(x.values.reshape(-1, 1))
        apps_clean.loc[amt_fill_rows, 'AMT_GOODS_PRICE'] = y

        # fit linear regression for annuity amount using credit amount and goods price]
        if fit_transform:
            amt_lr_rows = apps_clean['AMT_ANNUITY'].notna()
            x = apps_clean.loc[amt_lr_rows][['AMT_CREDIT', 'AMT_GOODS_PRICE']]
            y = apps_clean.loc[amt_lr_rows]['AMT_ANNUITY']
            self._amt_an_lr.fit(x.values, y)

        # use fitted linear regression to predict annuity amount
        amt_fill_rows = apps_clean['AMT_ANNUITY'].isna()
        x = apps_clean.loc[amt_fill_rows][['AMT_CREDIT', 'AMT_GOODS_PRICE']]
        y = self._amt_an_lr.predict(x.values)
        apps_clean.loc[amt_fill_rows, 'AMT_ANNUITY'] = y

        # basic mean imputation of remaining na values
        if fit_transform:
            self._mean_imp_cols = apps_clean.columns[apps_clean.isna().sum(axis=0) > 0]
            self._mean_imp_means = apps_clean[self._mean_imp_cols].mean()

        apps_clean[self._mean_imp_cols] = apps_clean[self._mean_imp_cols].fillna(self._mean_imp_means)

        return apps_clean


    def read_bureau(self):
        # read in credit bureau data
        bureau = pd.read_csv('{}/bureau.csv'.format(self._data_dir))
        bureau_clean = bureau.copy()

        # convert categorical columns to Categorical dtype
        bureau_clean = self._cat_data_dummies(bureau_clean)

        # sum up the numerical data for each applicant for a simple summary
        bureau_summary = bureau_clean.groupby('SK_ID_CURR').sum()

        return bureau_summary

    def read_previous_application(self):
        previous_application = pd.read_csv('{}/previous_application.csv'.format(self._data_dir))
        previous_clean = previous_application.copy()

        # convert categorical columns to Categorical dtype
        previous_clean = self._cat_data_dummies(previous_clean)

        # create summary of the data and join them together
        prev_app_summary = previous_clean.groupby('SK_ID_CURR').sum()
        return prev_app_summary

    def applications_train_index(self):
        return self._applications.index

    def _yn_cols_to_boolean(self, df, cols):
        yn_map = {'Y': 1,
                  'N': 0}
        return df[cols].replace(yn_map)

    def _cat_data(self, df):
        df_clean = df.copy()

        # detect columns with dtype 'object'
        cols = df.columns[df.dtypes == 'object']

        # fill na values with 'Unspecified'
        cat_na_count = df_clean[cols].isna().sum(axis=0)
        cat_na_map = {cat: 'Unspecified' for cat in cat_na_count[cat_na_count > 0].index}
        df_clean = df_clean.fillna(value=cat_na_map)

        # convert columns to categorical
        cat_labels = {}
        for cat_col in cols:
            cat_labels[cat_col] = df_clean[cat_col].unique()
            df_clean[cat_col] = pd.Categorical(df_clean[cat_col], categories=cat_labels[cat_col])

        return df_clean

    def _cat_data_dummies(self, df):
        df_clean = df.copy()

        # convert categorical columns to Categorical dtype
        df_clean = self._cat_data(df_clean)

        # convert Categorical columns to dummy columns
        df_clean = pd.get_dummies(df_clean)

        # return dataframe with dummy columns
        return df_clean
