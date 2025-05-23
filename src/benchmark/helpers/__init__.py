from benchmark.helpers.config.general import *
from benchmark.helpers.evaluation.metrics import compare_csv

CONFIG_DISPATCH = {
    "upload_file_to_vm": upload_file_to_vm,
}


EVAL_DISPATCH = {"compare_csv": compare_csv}
