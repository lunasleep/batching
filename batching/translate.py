import numpy as np
from operator import itemgetter
from sklearn.preprocessing import StandardScaler
import logging


def remove_false_anchors_factory(label):
    def remove_false_anchors(df):
        anchors = df[label].rolling(3).apply(lambda x: x[0] == 0 and x[1] == 1 and x[2] == 0, raw=True)
        anchors_idx = (np.where(anchors.values == 1)[0] - 1).tolist()
        if anchors_idx:
            df.iloc[anchors_idx, df.columns.get_loc(label)] = 0
        return df

    return remove_false_anchors


def split_flat_df_by_time_factory(look_back, look_forward, n_seconds):
    def split_flat_df_by_time_gaps(df):
        gap_idxs = np.where(np.diff(df["time"].values) != np.timedelta64(n_seconds, 's'))[0].tolist()
        if not gap_idxs:
            return [df]

        start_idx = 0
        valid_sections = []
        for gap_idx in gap_idxs:
            end_idx = gap_idx + 1
            if df.iloc[start_idx:end_idx].shape[0] >= (look_back + look_forward + 1):
                valid_sections.append(df.iloc[start_idx:end_idx])
            start_idx = end_idx
        if df.iloc[start_idx:].shape[0] >= (look_back + look_forward + 1):
            valid_sections.append(df.iloc[start_idx:])

        return valid_sections

    return split_flat_df_by_time_gaps


class Translate(object):
    def __init__(self,
                 features,
                 look_back,
                 look_forward,
                 n_seconds=1,
                 normalize=True,
                 verbose=False,
                 custom_transforms=None):
        self._features = features
        self._look_forward = look_forward
        self._look_back = look_back
        self._n_features = len(features)
        self._n_seconds = n_seconds
        self._normalize = normalize
        self._custom_transforms = custom_transforms

        self._verbose = verbose
        self._logger = logging.getLogger(__name__)

        self.scaler = StandardScaler()

    @property
    def look_back(self):
        return self._look_back

    @property
    def look_forward(self):
        return self._look_forward

    @property
    def time_steps(self):
        return self._look_back + self._look_forward + 1

    @property
    def num_features(self):
        return self._n_features

    def get_translate_params(self):
        params = {
            'features': self._features,
            'look_forward': self._look_forward,
            'look_back': self._look_back,
            'seconds_per_batch': self._n_seconds,
            'normalized': self._normalize,
            'mean': self.scaler.mean_.tolist() if self._normalize else [0] * len(self._features),
            'std': self.scaler.scale_.tolist() if self._normalize else [1] * len(self._features),
        }
        return params

    def set_translate_params(self, params):
        self._features = params["features"]
        self._look_forward = params["look_forward"]
        self._look_back = params["look_back"]
        self._n_seconds = params["seconds_per_batch"]
        self._normalize = params.get("normalized", True)
        self.scaler.mean_ = np.array(params["mean"])
        self.scaler.scale_ = np.array(params["std"])

    def _feature_df_to_nn_input(self, df):
        window_features = []
        x_start = self._look_back + self._look_forward
        y_start = self._look_back
        y_end = len(df["y"]) - self._look_forward

        for feature in self._features:
            feature_df = df[feature]
            ts_data = [feature_df.shift(i).values for i in range(self._look_back + self._look_forward, -1, -1)]
            window_features.append(np.vstack(ts_data)[:, x_start:])

        # transpose: (n_features, n_seconds, look_back) -> (n_seconds, look_back, n_features)
        return np.stack(window_features).transpose((2, 1, 0)), df.iloc[y_start:y_end]["y"]

    def normalize_dataset(self, session_df_list):
        if not self._normalize:
            return

        if self._verbose:
            self._logger.info("Scaling data")
        for session in session_df_list:
            if session.shape[0] > 0:
                self.scaler.partial_fit(session[self._features].astype('float64'))

    def scale_and_transform_session(self, session_df):
        clean_df = session_df[self._features + ["time", "y"]].dropna().copy()
        if self._normalize:
            clean_df.loc[:, self._features] = self.scaler.transform(clean_df[self._features])

        if self._custom_transforms:
            for transform in self._custom_transforms:
                clean_df = transform(clean_df)
        else:
            clean_df = [clean_df]

        if not clean_df:
            return np.array([]).reshape((0, self.time_steps, self.num_features)), np.array([])

        # reformat for sequence models based on window params
        sequences = list(map(self._feature_df_to_nn_input, clean_df))
        train_data = np.concatenate(list(map(itemgetter(0), sequences)), axis=0)
        train_truth = np.concatenate(list(map(itemgetter(1), sequences)), axis=0)
        return train_data, train_truth
