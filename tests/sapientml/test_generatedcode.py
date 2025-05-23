# Copyright 2023-2024 The SapientML Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import glob
import pickle
import tempfile
from importlib.metadata import entry_points
from pathlib import Path
from shutil import copyfile

import nbformat
import numpy as np
import pandas as pd
import pytest
from sapientml.executor import run
from sapientml.params import Task
from sapientml_core import SapientMLConfig
from sapientml_core.explain.code_miner import Miner

fxdir = Path("tests/fixtures").absolute()


@pytest.fixture(scope="function")
def test_data():
    return pd.read_csv(fxdir / "datasets" / "testdata_df.csv")


@pytest.fixture(scope="function")
def test_df_train():
    return pd.read_csv(fxdir / "datasets" / "testdata_train.csv")


@pytest.fixture(scope="function")
def test_df_valid():
    return pd.read_csv(fxdir / "datasets" / "testdata_valid.csv")


@pytest.fixture(scope="function")
def test_df_test():
    return pd.read_csv(fxdir / "datasets" / "testdata_test.csv")


@pytest.fixture(scope="function")
def setup_request_parameters():
    def _request_parameters():
        # with open(fxdir / "params" / "task.pkl", mode="rb") as f:
        #     task = pickle.load(f)
        task = Task(
            target_columns=["target_number"],
            task_type="regression",
            split_method="random",
            split_seed=17,
            split_train_size=0.75,
            split_column_name=None,
            time_split_num=5,
            time_split_index=4,
            adaptation_metric="r2",
            split_stratification=False,
        )
        # with open(fxdir / "params" / "config.pkl", mode="rb") as f:
        #     config = pickle.load(f)
        config = SapientMLConfig()
        with open(fxdir / "params" / "dataset.pkl", mode="rb") as f:
            dataset = pickle.load(f)
        return task, config, dataset

    return _request_parameters


@pytest.fixture(scope="function")
def make_tempdir(dir=fxdir / "outputs"):
    with tempfile.TemporaryDirectory(dir=dir) as temp_dir_path_str:
        temp_dir = Path(temp_dir_path_str).absolute()
        temp_dir.mkdir(exist_ok=True)
        yield temp_dir


@pytest.fixture(scope="function")
def execute_pipeline():
    def _execute(dataset, task, config, temp_dir, initial_timeout=60):
        eps = entry_points(group="sapientml.pipeline_generator")
        kwargs = config.model_dump()
        kwargs["initial_timeout"] = initial_timeout
        dataset.output_dir = temp_dir
        generator = eps["sapientml"].load()(**kwargs)

        # copy libs
        lib_path = dataset.output_dir / "lib"
        lib_path.mkdir(exist_ok=True)

        eps = entry_points(group="sapientml.export_modules")
        for ep in eps:
            if ep.name in [generator.__class__.__name__, "sample-dataset"]:
                for file in glob.glob(f"{ep.load().__path__[0]}/*.py"):
                    copyfile(file, lib_path / Path(file).name)

        generator.generate_pipeline(dataset, task)

        return generator.execution_results

    return _execute


@pytest.fixture(scope="function")
def execute_code_for_test():
    def _execute(pipeline_results, temp_dir):
        test_result_df = pd.DataFrame(
            index=range(len(pipeline_results)), columns=["returncode", "model", "result", "code_for_test"]
        )
        save_file_path = (temp_dir / "code_test.py").absolute().as_posix()
        for i in range(len(pipeline_results)):
            code_for_test = pipeline_results[i][0].test
            with open(save_file_path, "w", encoding="utf-8") as f:
                f.write(code_for_test)
            test_result = run(save_file_path, 300, None)
            test_result_df.loc[i, "returncode"] = test_result.returncode
            test_result_df.loc[i, "model"] = pipeline_results[i][0].model.label_name.split(":")[-2]
            test_result_df.loc[i, "result"] = test_result
            test_result_df.loc[i, "code_for_test"] = code_for_test
        return test_result_df

    return _execute


@pytest.fixture(scope="function")
def execute_code_for_test_ipynb():
    def _make_notebook_object(code):
        rtjnote = {"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 4}
        cell = {
            "cell_type": "code",
            "execution_count": None,
            "metadata": {},
            "outputs": [],
            "source": code,
        }
        rtjnote["cells"].append(cell)
        return rtjnote

    def _execute(pipeline_results, temp_dir):
        timeout = 0
        test_result_df = pd.DataFrame(
            index=range(len(pipeline_results)), columns=["returncode", "model", "exception", "code_for_test"]
        )

        save_file_path = (temp_dir / "code_test.ipynb").absolute().as_posix()
        for i in range(len(pipeline_results)):
            code_for_test = _make_notebook_object(pipeline_results[i][0].test)
            Miner.__save__(save_file_path, code_for_test)
            with open(save_file_path, "r", encoding="utf-8") as f:
                nb = nbformat.read(f, as_version=4)
            code_for_test = pipeline_results[i][0].test
            code_for_test_file = "code_test_" + str(i + 1) + ".py"
            code_for_test_file_path = (temp_dir / code_for_test_file).absolute().as_posix()
            with open(code_for_test_file_path, "w", encoding="utf-8") as f:
                f.write(code_for_test)
            try:
                Miner.execute_notebook(nb, {"metadata": {"path": "."}}, timeout, False)
                test_result_df.loc[i, "returncode"] = 0
                test_result_df.loc[i, "model"] = pipeline_results[i][0].model.label_name.split(":")[-2]
                test_result_df.loc[i, "exception"] = None
                test_result_df.loc[i, "code_for_test"] = pipeline_results[i][0].test
                with open(f"{save_file_path}.out.ipynb", "w", encoding="utf-8") as f:
                    nbformat.write(nb, f)
            except Exception as e:
                test_result_df.loc[i, "returncode"] = 1
                test_result_df.loc[i, "model"] = pipeline_results[i][0].model.label_name.split(":")[-2]
                test_result_df.loc[i, "code_for_test"] = pipeline_results[i][0].test
                test_result_df.loc[i, "exception"] = e
        return test_result_df

    return _execute


@pytest.mark.parametrize("adaptation_metric", ["r2", "RMSE"])
@pytest.mark.parametrize("target_col", ["target_number"])
def test_regressor_works_number(
    adaptation_metric,
    target_col,
    setup_request_parameters,
    make_tempdir,
    execute_pipeline,
    execute_code_for_test,
    execute_code_for_test_ipynb,
    test_data,
):
    task, config, dataset = setup_request_parameters()

    # test pattern setting
    df = test_data
    n_models = 14  # Maximum number of types in regressor_works is 14
    config.n_models = n_models

    task.task_type = "regression"
    task.adaptation_metric = adaptation_metric
    task.target_columns = [target_col]

    dataset.training_dataframe = df
    dataset.training_data_path = (fxdir / "datasets" / "testdata_df.csv").as_posix()

    temp_dir = make_tempdir
    pipeline_results = execute_pipeline(dataset, task, config, temp_dir, initial_timeout=60)
    test_result_df = execute_code_for_test_ipynb(pipeline_results, temp_dir)

    for i in range(len(test_result_df)):
        model = test_result_df.loc[i, "model"]
        returncode = test_result_df.loc[i, "returncode"]
        exception = test_result_df.loc[i, "exception"]
        if model == "SVR":
            # "AttributeError:var not found" occurs in SVR because of sparse_matrix
            assert returncode == 1
        elif model == "XGBRegressor" and (exception is not None and "ExplainerError" in exception.ename):
            # There is a known (rare) issue with the interaction between SHAP and the XGBoost library,
            # which may cause SHAP to add slightly incorrect values.
            # Most XGBoost models generate SHAP values following addition and are validated by automatic checking.
            # If the violation in the automatic check is large, a shap.utils.ExplainerError occurs.
            assert returncode == 1
        else:
            assert returncode == 0


@pytest.mark.parametrize("adaptation_metric", ["MAE"])
@pytest.mark.parametrize("target_col", ["target_number"])
def test_regressor_works_with_nosparse(
    adaptation_metric,
    target_col,
    setup_request_parameters,
    make_tempdir,
    execute_pipeline,
    execute_code_for_test_ipynb,
    test_data,
):
    # Test with no sparse df
    task, config, dataset = setup_request_parameters()

    # test pattern setting
    df = test_data
    n_models = 14  # Maximum number of types in regressor_works is 14
    config.n_models = n_models

    task.task_type = "regression"
    task.adaptation_metric = adaptation_metric
    task.target_columns = [target_col]
    dataset.ignore_columns.extend(
        ["explanatory_text_english", "explanatory_text_japanese", "explanatory_json", "explanatory_list"]
    )

    dataset.training_dataframe = df
    dataset.training_data_path = (fxdir / "datasets" / "testdata_df.csv").as_posix()
    # dataset.meta_features_pp["feature:str_text_presence"] = 0.0

    temp_dir = make_tempdir
    pipeline_results = execute_pipeline(dataset, task, config, temp_dir, initial_timeout=60)
    test_result_df = execute_code_for_test_ipynb(pipeline_results, temp_dir)
    for i in range(len(test_result_df)):
        model = test_result_df.loc[i, "model"]
        returncode = test_result_df.loc[i, "returncode"]
        exception = test_result_df.loc[i, "exception"]
        if model == "XGBRegressor" and (exception is not None and "ExplainerError" in exception.ename):
            # There is a known (rare) issue with the interaction between SHAP and the XGBoost library,
            # which may cause SHAP to add slightly incorrect values.
            # Most XGBoost models generate SHAP values following addition and are validated by automatic checking.
            # If the violation in the automatic check is large, a shap.utils.ExplainerError occurs.
            assert returncode == 1
        else:
            assert returncode == 0


@pytest.mark.parametrize("adaptation_metric", ["f1"])
@pytest.mark.parametrize("target_col", ["target_category_binary_num"])
def test_classifier_category_binary_num_noproba(
    adaptation_metric,
    target_col,
    setup_request_parameters,
    make_tempdir,
    execute_pipeline,
    execute_code_for_test_ipynb,
    test_data,
):
    task, config, dataset = setup_request_parameters()

    # test pattern setting
    df = test_data
    n_models = 16
    config.n_models = n_models

    task.task_type = "classification"
    task.adaptation_metric = adaptation_metric
    task.target_columns = [target_col]
    if df[target_col].nunique() > 2:
        task.is_multiclass = True
    else:
        task.is_multiclass = False

    dataset.training_dataframe = df
    dataset.training_data_path = (fxdir / "datasets" / "testdata_df.csv").as_posix()

    temp_dir = make_tempdir
    pipeline_results = execute_pipeline(dataset, task, config, temp_dir, initial_timeout=60)
    test_result_df = execute_code_for_test_ipynb(pipeline_results, temp_dir)
    for i in range(len(test_result_df)):
        model = test_result_df.loc[i, "model"]
        returncode = test_result_df.loc[i, "returncode"]
        if model == "LinearSVC":
            # AttributeError: 'LinearSVC' object has no attribute 'predict_proba'
            assert returncode == 1
        elif model == "SGDClassifier":
            # AttributeError: probability estimates are not available for loss='hinge' (‘hinge’ gives a linear SVM.)
            assert returncode == 1
        elif model == "SVC":
            # "AttributeError:var not found" occurs in SVC because of sparse_matrix
            assert returncode == 1
        elif model == "GaussianNB":
            # Sparse matrix is not supported
            assert returncode == 1
        elif model == "MultinomialNB":
            # Negative value is not supported
            assert returncode == 1
        else:
            assert returncode == 0


@pytest.mark.parametrize("adaptation_metric", ["auc", "LogLoss"])
@pytest.mark.parametrize("target_col", ["target_category_binary_num"])
def test_classifier_category_binary_num_proba(
    adaptation_metric,
    target_col,
    setup_request_parameters,
    make_tempdir,
    execute_pipeline,
    execute_code_for_test_ipynb,
    test_data,
):
    task, config, dataset = setup_request_parameters()

    # test pattern setting
    df = test_data
    n_models = 16
    config.n_models = n_models

    task.task_type = "classification"
    task.adaptation_metric = adaptation_metric
    task.target_columns = [target_col]
    if df[target_col].nunique() > 2:
        task.is_multiclass = True
    else:
        task.is_multiclass = False

    dataset.training_dataframe = df
    dataset.training_data_path = (fxdir / "datasets" / "testdata_df.csv").as_posix()

    temp_dir = make_tempdir
    pipeline_results = execute_pipeline(dataset, task, config, temp_dir, initial_timeout=60)
    test_result_df = execute_code_for_test_ipynb(pipeline_results, temp_dir)
    for i in range(len(test_result_df)):
        model = test_result_df.loc[i, "model"]
        returncode = test_result_df.loc[i, "returncode"]
        if model == "LinearSVC":
            # AttributeError: 'LinearSVC' object has no attribute 'predict_proba'
            assert returncode == 1
        elif model == "SGDClassifier":
            # AttributeError: probability estimates are not available for loss='hinge' (‘hinge’ gives a linear SVM.)
            assert returncode == 1
        elif model == "SVC":
            # "AttributeError:var not found" occurs in SVC because of sparse_matrix
            assert returncode == 1
        elif model == "GaussianNB":
            # Sparse matrix is not supported
            assert returncode == 1
        elif model == "MultinomialNB":
            # Negative value is not supported
            assert returncode == 1
        else:
            assert returncode == 0


@pytest.mark.parametrize("adaptation_metric", ["f1", "MCC"])
@pytest.mark.parametrize("target_col", ["target_category_multi_nonnum"])
def test_classifier_category_multi_nonnum_metric_noproba(
    adaptation_metric,
    target_col,
    setup_request_parameters,
    make_tempdir,
    execute_pipeline,
    execute_code_for_test_ipynb,
    test_data,
):
    task, config, dataset = setup_request_parameters()

    # test pattern setting
    df = test_data
    n_models = 16
    config.n_models = n_models

    task.task_type = "classification"
    task.adaptation_metric = adaptation_metric
    task.target_columns = [target_col]
    if df[target_col].nunique() > 2:
        task.is_multiclass = True
    else:
        task.is_multiclass = False

    dataset.training_dataframe = df
    dataset.training_data_path = (fxdir / "datasets" / "testdata_df.csv").as_posix()

    temp_dir = make_tempdir
    pipeline_results = execute_pipeline(dataset, task, config, temp_dir, initial_timeout=60)
    test_result_df = execute_code_for_test_ipynb(pipeline_results, temp_dir)
    for i in range(len(test_result_df)):
        model = test_result_df.loc[i, "model"]
        returncode = test_result_df.loc[i, "returncode"]
        if model == "LinearSVC":
            # AttributeError: 'LinearSVC' object has no attribute 'predict_proba'
            assert returncode == 1
        elif model == "SGDClassifier":
            # AttributeError: probability estimates are not available for loss='hinge' (‘hinge’ gives a linear SVM.)
            assert returncode == 1
        elif model == "SVC":
            # "AttributeError:var not found" occurs in SVC because of sparse_matrix
            assert returncode == 1
        elif model == "GaussianNB":
            # Sparse matrix is not supported
            assert returncode == 1
        elif model == "MultinomialNB":
            # Negative value is not supported
            assert returncode == 1
        else:
            assert returncode == 0


@pytest.mark.parametrize("adaptation_metric", ["ROC_AUC", "Gini", "MAP_3"])
@pytest.mark.parametrize("target_col", ["target_category_multi_nonnum"])
def test_classifier_category_multi_nonnum_metric_proba(
    adaptation_metric,
    target_col,
    setup_request_parameters,
    make_tempdir,
    execute_pipeline,
    execute_code_for_test_ipynb,
    test_data,
):
    task, config, dataset = setup_request_parameters()

    # test pattern setting
    df = test_data
    n_models = 16
    config.n_models = n_models

    task.task_type = "classification"
    task.adaptation_metric = adaptation_metric
    task.target_columns = [target_col]
    if df[target_col].nunique() > 2:
        task.is_multiclass = True
    else:
        task.is_multiclass = False

    dataset.training_dataframe = df
    dataset.training_data_path = (fxdir / "datasets" / "testdata_df.csv").as_posix()

    temp_dir = make_tempdir
    pipeline_results = execute_pipeline(dataset, task, config, temp_dir, initial_timeout=60)
    test_result_df = execute_code_for_test_ipynb(pipeline_results, temp_dir)
    for i in range(len(test_result_df)):
        model = test_result_df.loc[i, "model"]
        returncode = test_result_df.loc[i, "returncode"]
        if model == "LinearSVC":
            # AttributeError: 'LinearSVC' object has no attribute 'predict_proba'
            assert returncode == 1
        elif model == "SGDClassifier":
            # AttributeError: probability estimates are not available for loss='hinge' (‘hinge’ gives a linear SVM.)
            assert returncode == 1
        elif model == "SVC":
            # "AttributeError:var not found" occurs in SVC because of sparse_matrix
            assert returncode == 1
        elif model == "GaussianNB":
            # Sparse matrix is not supported
            assert returncode == 1
        elif model == "MultinomialNB":
            # Negative value is not supported
            assert returncode == 1
        else:
            assert returncode == 0


@pytest.mark.parametrize("adaptation_metric", ["f1", "QWK"])
@pytest.mark.parametrize("target_col", ["target_category_binary_boolean"])
def test_classifier_category_binary_boolean_metric_noproba(
    adaptation_metric,
    target_col,
    setup_request_parameters,
    make_tempdir,
    execute_pipeline,
    execute_code_for_test_ipynb,
    test_data,
):
    task, config, dataset = setup_request_parameters()

    # test pattern setting
    df = test_data
    n_models = 16
    config.n_models = n_models

    task.task_type = "classification"
    task.adaptation_metric = adaptation_metric
    task.target_columns = [target_col]
    if df[target_col].nunique() > 2:
        task.is_multiclass = True
    else:
        task.is_multiclass = False

    dataset.training_dataframe = df
    dataset.training_data_path = (fxdir / "datasets" / "testdata_df.csv").as_posix()

    temp_dir = make_tempdir
    pipeline_results = execute_pipeline(dataset, task, config, temp_dir, initial_timeout=60)
    test_result_df = execute_code_for_test_ipynb(pipeline_results, temp_dir)
    for i in range(len(test_result_df)):
        model = test_result_df.loc[i, "model"]
        returncode = test_result_df.loc[i, "returncode"]
        if model == "LinearSVC":
            # AttributeError: 'LinearSVC' object has no attribute 'predict_proba'
            assert returncode == 1
        elif model == "SGDClassifier":
            # AttributeError: probability estimates are not available for loss='hinge' (‘hinge’ gives a linear SVM.)
            assert returncode == 1
        elif model == "SVC":
            # "AttributeError:var not found" occurs in SVC because of sparse_matrix
            assert returncode == 1
        elif model == "GaussianNB":
            # Sparse matrix is not supported
            assert returncode == 1
        elif model == "MultinomialNB":
            # Negative value is not supported
            assert returncode == 1
        else:
            assert returncode == 0


@pytest.mark.parametrize("adaptation_metric", ["auc", "LogLoss"])
@pytest.mark.parametrize("target_col", ["target_category_binary_boolean"])
def test_classifier_category_binary_boolean_metric_proba(
    adaptation_metric,
    target_col,
    setup_request_parameters,
    make_tempdir,
    execute_pipeline,
    execute_code_for_test_ipynb,
    test_data,
):
    task, config, dataset = setup_request_parameters()

    # test pattern setting
    df = test_data
    n_models = 16
    config.n_models = n_models

    task.task_type = "classification"
    task.adaptation_metric = adaptation_metric
    task.target_columns = [target_col]
    if df[target_col].nunique() > 2:
        task.is_multiclass = True
    else:
        task.is_multiclass = False

    dataset.training_dataframe = df
    dataset.training_data_path = (fxdir / "datasets" / "testdata_df.csv").as_posix()

    temp_dir = make_tempdir
    pipeline_results = execute_pipeline(dataset, task, config, temp_dir, initial_timeout=60)
    test_result_df = execute_code_for_test_ipynb(pipeline_results, temp_dir)
    for i in range(len(test_result_df)):
        model = test_result_df.loc[i, "model"]
        returncode = test_result_df.loc[i, "returncode"]
        if model == "LinearSVC":
            # AttributeError: 'LinearSVC' object has no attribute 'predict_proba'
            assert returncode == 1
        elif model == "SGDClassifier":
            # AttributeError: probability estimates are not available for loss='hinge' (‘hinge’ gives a linear SVM.)
            assert returncode == 1
        elif model == "SVC":
            # "AttributeError:var not found" occurs in SVC because of sparse_matrix
            assert returncode == 1
        elif model == "GaussianNB":
            # Sparse matrix is not supported
            assert returncode == 1
        elif model == "MultinomialNB":
            # Negative value is not supported
            assert returncode == 1
        else:
            assert returncode == 0


@pytest.mark.parametrize("adaptation_metric", ["f1"])
@pytest.mark.parametrize("target_col", ["target_category_binary_has_japanese"])
def test_classifier_works_with_target_pattern(
    adaptation_metric,
    target_col,
    setup_request_parameters,
    make_tempdir,
    execute_pipeline,
    execute_code_for_test_ipynb,
    test_data,
):
    task, config, dataset = setup_request_parameters()

    # test pattern setting
    df = test_data
    n_models = 16
    config.n_models = n_models

    task.task_type = "classification"
    task.adaptation_metric = adaptation_metric
    task.target_columns = [target_col]
    if df[target_col].nunique() > 2:
        task.is_multiclass = True
    else:
        task.is_multiclass = False

    dataset.training_dataframe = df
    dataset.training_data_path = (fxdir / "datasets" / "testdata_df.csv").as_posix()
    temp_dir = make_tempdir
    pipeline_results = execute_pipeline(dataset, task, config, temp_dir, initial_timeout=60)
    test_result_df = execute_code_for_test_ipynb(pipeline_results, temp_dir)
    for i in range(len(test_result_df)):
        model = test_result_df.loc[i, "model"]
        returncode = test_result_df.loc[i, "returncode"]
        if model == "LinearSVC":
            # AttributeError: 'LinearSVC' object has no attribute 'predict_proba'
            assert returncode == 1
        elif model == "SGDClassifier":
            # AttributeError: probability estimates are not available for loss='hinge' (‘hinge’ gives a linear SVM.)
            assert returncode == 1
        elif model == "SVC":
            # "AttributeError:var not found" occurs in SVC because of sparse_matrix
            assert returncode == 1
        elif model == "GaussianNB":
            # Sparse matrix is not supported
            assert returncode == 1
        elif model == "MultinomialNB":
            # Negative value is not supported
            assert returncode == 1
        else:
            assert returncode == 0


@pytest.mark.parametrize("adaptation_metric", ["f1"])
@pytest.mark.parametrize("target_col", ["target_category_binary_imbalance"])
def test_classifier_works_with_preprocess(
    adaptation_metric,
    target_col,
    setup_request_parameters,
    make_tempdir,
    execute_pipeline,
    execute_code_for_test_ipynb,
    test_data,
):
    task, config, dataset = setup_request_parameters()

    # test pattern setting
    df = test_data
    n_models = 1
    config.n_models = n_models

    task.task_type = "classification"
    task.adaptation_metric = adaptation_metric
    task.target_columns = [target_col]
    if df[target_col].nunique() > 2:
        task.is_multiclass = True
    else:
        task.is_multiclass = False

    dataset.training_dataframe = df
    dataset.training_data_path = (fxdir / "datasets" / "testdata_df.csv").as_posix()
    dataset.ignore_columns.extend(
        ["explanatory_multi_category_num", "target_category_multi_num", "target_category_binary_num"]
    )
    temp_dir = make_tempdir
    pipeline_results = execute_pipeline(dataset, task, config, temp_dir, initial_timeout=60)
    test_result_df = execute_code_for_test_ipynb(pipeline_results, temp_dir)
    code_for_test = test_result_df.loc[0, "code_for_test"]
    assert "SMOTE" in code_for_test
    assert "StandardScaler" in code_for_test


@pytest.mark.parametrize("adaptation_metric", ["f1"])
@pytest.mark.parametrize("target_col", ["target_category_multi_nonnum"])
def test_classifier_notext_nonegative_explanatry(
    adaptation_metric,
    target_col,
    setup_request_parameters,
    make_tempdir,
    execute_pipeline,
    execute_code_for_test_ipynb,
    test_data,
):
    task, config, dataset = setup_request_parameters()

    # test pattern setting
    df = test_data
    n_models = 16
    config.n_models = n_models

    task.task_type = "classification"
    task.adaptation_metric = adaptation_metric
    task.target_columns = [target_col]
    use_cols = [target_col] + ["explanatory_number", "explanatory_multi_category_nonnum"]
    ignore_cols = [col for col in df.columns if col not in use_cols]
    dataset.ignore_columns.extend(ignore_cols)
    dataset.ignore_columns = list(set(dataset.ignore_columns))

    dataset.training_dataframe = df
    dataset.training_data_path = (fxdir / "datasets" / "testdata_df.csv").as_posix()
    if "multi" in target_col:
        task.is_multiclass = True
    else:
        task.is_multiclass = False

    temp_dir = make_tempdir
    pipeline_results = execute_pipeline(dataset, task, config, temp_dir, initial_timeout=60)
    test_result_df = execute_code_for_test_ipynb(pipeline_results, temp_dir)
    for i in range(len(test_result_df)):
        model = test_result_df.loc[i, "model"]
        returncode = test_result_df.loc[i, "returncode"]
        if model == "LinearSVC":
            # AttributeError: 'LinearSVC' object has no attribute 'predict_proba'
            assert returncode == 1
        elif model == "SGDClassifier":
            # AttributeError: probability estimates are not available for loss='hinge' (‘hinge’ gives a linear SVM.)
            assert returncode == 1
        elif model == "SVC":
            # "AttributeError: 'SVC' object has no attribute 'predict_proba'
            assert returncode == 1
        else:
            assert returncode == 0


@pytest.mark.parametrize("adaptation_metric", ["f1", "accuracy"])
@pytest.mark.parametrize("target_col", ["target_category_binary_num"])
def test_classifier_category_binary_num_use_proba_with_metric_default_noproba(
    adaptation_metric,
    target_col,
    setup_request_parameters,
    make_tempdir,
    execute_pipeline,
    execute_code_for_test_ipynb,
    test_data,
):
    task, config, dataset = setup_request_parameters()

    # test pattern setting
    df = test_data
    n_models = 16
    config.n_models = n_models

    task.task_type = "classification"
    task.adaptation_metric = adaptation_metric
    task.target_columns = [target_col]
    config.predict_option = "probability"
    if "multi" in target_col:
        task.is_multiclass = True
    else:
        task.is_multiclass = False

    dataset.training_dataframe = df
    dataset.training_data_path = (fxdir / "datasets" / "testdata_df.csv").as_posix()

    temp_dir = make_tempdir
    pipeline_results = execute_pipeline(dataset, task, config, temp_dir, initial_timeout=60)
    test_result_df = execute_code_for_test_ipynb(pipeline_results, temp_dir)
    for i in range(len(test_result_df)):
        model = test_result_df.loc[i, "model"]
        returncode = test_result_df.loc[i, "returncode"]
        if model == "LinearSVC":
            # AttributeError: 'LinearSVC' object has no attribute 'predict_proba'
            assert returncode == 1
        elif model == "SGDClassifier":
            # AttributeError: probability estimates are not available for loss='hinge' (‘hinge’ gives a linear SVM.)
            assert returncode == 1
        elif model == "SVC":
            # "AttributeError:var not found" occurs in SVC because of sparse_matrix
            assert returncode == 1
        elif model == "GaussianNB":
            # Sparse matrix is not supported
            assert returncode == 1
        elif model == "MultinomialNB":
            # Negative value is not supported
            assert returncode == 1
        else:
            assert returncode == 0


@pytest.mark.parametrize("adaptation_metric", ["f1", "accuracy"])
@pytest.mark.parametrize("target_col", ["target_category_multi_nonnum"])
def test_classifier_category_multi_nonnum_noproba_metric_with_proba(
    adaptation_metric,
    target_col,
    setup_request_parameters,
    make_tempdir,
    execute_pipeline,
    execute_code_for_test_ipynb,
    test_data,
):
    task, config, dataset = setup_request_parameters()

    # test pattern setting
    df = test_data
    n_models = 16
    config.n_models = n_models

    task.task_type = "classification"
    task.adaptation_metric = adaptation_metric
    task.target_columns = [target_col]
    config.predict_option = "probability"
    if "multi" in target_col:
        task.is_multiclass = True
    else:
        task.is_multiclass = False

    dataset.training_dataframe = df
    dataset.training_data_path = (fxdir / "datasets" / "testdata_df.csv").as_posix()

    temp_dir = make_tempdir
    pipeline_results = execute_pipeline(dataset, task, config, temp_dir, initial_timeout=60)
    test_result_df = execute_code_for_test_ipynb(pipeline_results, temp_dir)
    for i in range(len(test_result_df)):
        model = test_result_df.loc[i, "model"]
        returncode = test_result_df.loc[i, "returncode"]
        if model == "LinearSVC":
            # AttributeError: 'LinearSVC' object has no attribute 'predict_proba'
            assert returncode == 1
        elif model == "SGDClassifier":
            # AttributeError: probability estimates are not available for loss='hinge' (‘hinge’ gives a linear SVM.)
            assert returncode == 1
        elif model == "SVC":
            # "AttributeError:var not found" occurs in SVC because of sparse_matrix
            assert returncode == 1
        elif model == "GaussianNB":
            # Sparse matrix is not supported
            assert returncode == 1
        elif model == "MultinomialNB":
            # Negative value is not supported
            assert returncode == 1
        else:
            assert returncode == 0


@pytest.mark.parametrize("adaptation_metric", ["RMSLE"])
@pytest.mark.parametrize("target_col", ["target_number_large_scale"])
def test_misc_preprocess_specify_train_valid_test(
    adaptation_metric,
    target_col,
    setup_request_parameters,
    make_tempdir,
    execute_pipeline,
    execute_code_for_test_ipynb,
    test_df_train,
    test_df_valid,
    test_df_test,
):
    task, config, dataset = setup_request_parameters()

    # test pattern setting
    n_models = 1
    config.n_models = n_models

    task.task_type = "regression"
    task.adaptation_metric = adaptation_metric
    task.target_columns = [target_col]

    dataset.training_dataframe = test_df_train
    dataset.validation_dataframe = test_df_valid
    dataset.test_dataframe = test_df_test

    dataset.training_data_path = (fxdir / "datasets" / "testdata_train.csv").as_posix()
    dataset.validation_data_path = (fxdir / "datasets" / "testdata_valid.csv").as_posix()
    dataset.test_data_path = (fxdir / "datasets" / "testdata_test.csv").as_posix()

    # Exclude columns that prevent the applying Scaling:log preprocessing
    dataset.ignore_columns.extend(
        ["explanatory_multi_category_num", "target_category_multi_num", "target_category_binary_num"]
    )
    temp_dir = make_tempdir
    pipeline_results = execute_pipeline(dataset, task, config, temp_dir, initial_timeout=60)
    test_result_df = execute_code_for_test_ipynb(pipeline_results, temp_dir)

    for i in range(len(test_result_df)):
        model = test_result_df.loc[i, "model"]
        returncode = test_result_df.loc[i, "returncode"]
        code_for_test = test_result_df.loc[i, "code_for_test"]
        exception = test_result_df.loc[i, "exception"]

        assert "TRAIN-TEST SPLIT" not in code_for_test
        assert "Remove special symbols" in code_for_test
        assert "HANDLE MIXED TYPE" in code_for_test
        assert "CONVERT INF TO NAN" in code_for_test
        assert "HANDLE JAPANESE TEXT" in code_for_test
        assert "DISCARD IRRELEVANT COLUMNS" in code_for_test
        assert "Preprocess:SimpleImputer" in code_for_test
        assert "NUMERIC_COLS_WITH_MISSING_VALUES" in code_for_test
        assert "NUMERIC_ALMOST_MISSING_COLS" in code_for_test
        assert "STRING_COLS_WITH_MISSING_VALUES" in code_for_test
        assert "STRING_ALMOST_MISSING_COLS" in code_for_test
        assert "Preprocess:OrdinalEncoder" in code_for_test
        assert "Preprocess:DateTime" in code_for_test
        assert "Preprocess:TextPreprocessing" in code_for_test
        assert "Preprocess:TfidfVectorizer" in code_for_test
        # assert "np.log" in code_for_test
        if model == "SVR":
            # "AttributeError:var not found" occurs in SVR because of sparse_matrix
            assert returncode == 1
        elif model == "XGBRegressor" and (exception is not None and "ExplainerError" in exception.ename):
            # There is a known (rare) issue with the interaction between SHAP and the XGBoost library,
            # which may cause SHAP to add slightly incorrect values.
            # Most XGBoost models generate SHAP values following addition and are validated by automatic checking.
            # If the violation in the automatic check is large, a shap.utils.ExplainerError occurs.
            assert returncode == 1
        else:
            assert returncode == 0


def test_misc_sapientml_works_initial_timeout(setup_request_parameters, make_tempdir, execute_pipeline, test_data):
    task, config, dataset = setup_request_parameters()

    # test pattern setting
    df = test_data
    n_models = 3
    config.n_models = n_models

    task.task_type = "regression"
    task.target_columns = ["target_number"]

    dataset.training_dataframe = df
    dataset.training_data_path = (fxdir / "datasets" / "testdata_df.csv").as_posix()

    temp_dir = make_tempdir
    pipeline_results = execute_pipeline(dataset, task, config, temp_dir, initial_timeout=1)
    for i in range(len(pipeline_results)):
        error = pipeline_results[i][1].error
        returncode = pipeline_results[i][1].returncode
        if error:
            assert (error == "Timeout") and (returncode == -9)


def test_misc_timeout_works_hyperparameter_tuning_timeout(
    setup_request_parameters, make_tempdir, execute_pipeline, test_data
):
    task, config, dataset = setup_request_parameters()

    # test pattern setting
    df = test_data
    n_models = 1
    config.n_models = n_models

    task.task_type = "regression"
    task.target_columns = ["target_number"]

    config.hyperparameter_tuning = True
    initial_timeout = 0 if config.hyperparameter_tuning else 60
    config.hyperparameter_tuning_n_trials = 10
    config.hyperparameter_tuning_timeout = 1

    dataset.training_dataframe = df
    dataset.training_data_path = (fxdir / "datasets" / "testdata_df.csv").as_posix()

    temp_dir = make_tempdir
    pipeline_results = execute_pipeline(dataset, task, config, temp_dir, initial_timeout=initial_timeout)

    assert pipeline_results[0][1].returncode == 0


@pytest.mark.parametrize("invalid_target_value", [np.nan, np.inf, -np.inf])
@pytest.mark.parametrize("invalid_count", ["half", "one"])
def test_nan_or_inf_in_target_columns_for_train_data(
    invalid_target_value,
    invalid_count,
    setup_request_parameters,
    make_tempdir,
    execute_pipeline,
    test_df_train,
    test_df_valid,
    test_df_test,
):
    task, config, dataset = setup_request_parameters()

    # test pattern setting
    n_models = 1
    config.n_models = n_models

    task.task_type = "regression"
    task.adaptation_metric = "RMSLE"
    task.target_columns = ["target_number_large_scale"]

    dataset.training_dataframe = test_df_train
    dataset.validation_dataframe = test_df_valid
    dataset.test_dataframe = test_df_test

    dataset.training_data_path = (fxdir / "datasets" / "testdata_train.csv").as_posix()
    dataset.validation_data_path = (fxdir / "datasets" / "testdata_valid.csv").as_posix()
    dataset.test_data_path = (fxdir / "datasets" / "testdata_test.csv").as_posix()

    temp_dir = make_tempdir
    if invalid_count == "half":
        dataset.training_dataframe.loc[0::2, task.target_columns] = invalid_target_value
        with pytest.raises(ValueError) as e:
            execute_pipeline(dataset, task, config, temp_dir, initial_timeout=60)
        assert "dataframe has more than 10 rows including NaN or Inf." in str(e.value)
    else:
        dataset.training_dataframe.loc[dataset.training_dataframe.index[0], task.target_columns] = invalid_target_value
        pipeline_results = execute_pipeline(dataset, task, config, temp_dir, initial_timeout=60)
        assert not (pipeline_results is None)


@pytest.mark.parametrize("invalid_target_value", [np.nan, np.inf, -np.inf])
def test_nan_or_inf_in_target_columns_for_validation_data(
    invalid_target_value,
    setup_request_parameters,
    make_tempdir,
    execute_pipeline,
    test_df_train,
    test_df_valid,
    test_df_test,
):
    task, config, dataset = setup_request_parameters()

    # test pattern setting
    n_models = 1
    config.n_models = n_models

    task.task_type = "regression"
    task.adaptation_metric = "RMSLE"
    task.target_columns = ["target_number_large_scale"]

    dataset.training_dataframe = test_df_train
    dataset.validation_dataframe = test_df_valid
    dataset.test_dataframe = test_df_test

    dataset.training_data_path = (fxdir / "datasets" / "testdata_train.csv").as_posix()
    dataset.validation_data_path = (fxdir / "datasets" / "testdata_valid.csv").as_posix()
    dataset.test_data_path = (fxdir / "datasets" / "testdata_test.csv").as_posix()

    dataset.validation_dataframe.loc[dataset.validation_dataframe.index[0], task.target_columns] = invalid_target_value

    temp_dir = make_tempdir
    with pytest.raises(ValueError) as e:
        execute_pipeline(dataset, task, config, temp_dir, initial_timeout=60)

    assert "dataframe has NaN or Inf" in str(e.value)


@pytest.mark.parametrize("invalid_target_value", [np.nan, np.inf, -np.inf])
def test_nan_or_inf_in_target_columns_for_test_data(
    invalid_target_value,
    setup_request_parameters,
    make_tempdir,
    execute_pipeline,
    test_df_train,
    test_df_valid,
    test_df_test,
):
    task, config, dataset = setup_request_parameters()

    # test pattern setting
    n_models = 1
    config.n_models = n_models

    task.task_type = "regression"
    task.adaptation_metric = "RMSLE"
    task.target_columns = ["target_number_large_scale"]

    dataset.training_dataframe = test_df_train
    dataset.validation_dataframe = test_df_valid
    dataset.test_dataframe = test_df_test

    dataset.training_data_path = (fxdir / "datasets" / "testdata_train.csv").as_posix()
    dataset.validation_data_path = (fxdir / "datasets" / "testdata_valid.csv").as_posix()
    dataset.test_data_path = (fxdir / "datasets" / "testdata_test.csv").as_posix()

    dataset.test_dataframe.loc[dataset.test_dataframe.index[0], task.target_columns] = invalid_target_value

    temp_dir = make_tempdir
    with pytest.raises(ValueError) as e:
        execute_pipeline(dataset, task, config, temp_dir, initial_timeout=60)

    assert "dataframe has NaN or Inf" in str(e.value)
