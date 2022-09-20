from typing import Dict, List, Sequence
import os
import pytest
import shutil
from fnmatch import fnmatch
from prometheus_client import CollectorRegistry
from multiprocessing.dummy import Pool as ThreadPool

from dlt.common import json
from dlt.common.utils import uniq_id
from dlt.common.typing import StrAny
from dlt.common.file_storage import FileStorage
from dlt.common.schema import TDataType
from dlt.common.storages import NormalizeStorage, LoadStorage
from dlt.extract.extractor_storage import ExtractorStorageBase

from dlt.normalize import Normalize, configuration as normalize_configuration, __version__

from tests.cases import JSON_TYPED_DICT, JSON_TYPED_DICT_TYPES
from tests.utils import TEST_STORAGE_ROOT, assert_no_dict_key_starts_with, write_version, clean_test_storage, init_logger
from tests.normalize.utils import json_case_path


@pytest.fixture()
def raw_normalize() -> Normalize:
    # does not install default schemas, so no type hints and row filters
    # uses default json normalizer that does not yield additional rasa tables
    return init_normalize()


@pytest.fixture
def rasa_normalize() -> Normalize:
    # install default schemas, includes type hints and row filters
    # uses rasa json normalizer that yields event table and separate tables for event types
    return init_normalize("tests/normalize/cases/schemas")


def init_normalize(default_schemas_path: str = None) -> Normalize:
    clean_test_storage()
    initial = {}
    if default_schemas_path:
        initial = {"IMPORT_SCHEMA_PATH": default_schemas_path, "EXTERNAL_SCHEMA_FORMAT": "json"}
    n = Normalize(normalize_configuration(initial), CollectorRegistry())
    # set jsonl as default writer
    n.load_storage.preferred_file_format = n.CONFIG.LOADER_FILE_FORMAT = "jsonl"
    return n


@pytest.fixture(scope="module", autouse=True)
def logger_autouse() -> None:
    init_logger()


def test_intialize(rasa_normalize: Normalize) -> None:
    # create storages in fixture
    pass


# def test_empty_schema_name(raw_normalize: Normalize) -> None:
#     schema = raw_normalize.load_or_create_schema("")
#     assert schema.name == ""


def test_normalize_single_user_event_jsonl(raw_normalize: Normalize) -> None:
    expected_tables, load_files = normalize_event_user(raw_normalize, "event_user_load_1", EXPECTED_USER_TABLES)
    # load, parse and verify jsonl
    for expected_table in expected_tables:
        expect_lines_file(raw_normalize.load_storage, load_files[expected_table])
    # return first line from event_user file
    event_text, lines = expect_lines_file(raw_normalize.load_storage, load_files["event"], 0)
    assert lines == 1
    event_json = json.loads(event_text)
    assert event_json["event"] == "user"
    assert event_json["parse_data__intent__name"] == "greet"
    assert event_json["text"] == "hello"
    event_text, lines = expect_lines_file(raw_normalize.load_storage, load_files["event__parse_data__response_selector__default__ranking"], 9)
    assert lines == 10
    event_json = json.loads(event_text)
    assert "id" in event_json
    assert "confidence" in event_json
    assert "intent_response_key" in event_json


def test_normalize_single_user_event_insert(raw_normalize: Normalize) -> None:
    raw_normalize.load_storage.preferred_file_format = raw_normalize.CONFIG.LOADER_FILE_FORMAT = "insert_values"
    expected_tables, load_files = normalize_event_user(raw_normalize, "event_user_load_1", EXPECTED_USER_TABLES)
    # verify values line
    for expected_table in expected_tables:
        expect_lines_file(raw_normalize.load_storage, load_files[expected_table])
    # return first values line from event_user file
    event_text, lines = expect_lines_file(raw_normalize.load_storage, load_files["event"], 2)
    assert lines == 3
    assert "'user'" in  event_text
    assert "'greet'" in event_text
    assert "'hello'" in event_text
    event_text, lines = expect_lines_file(raw_normalize.load_storage, load_files["event__parse_data__response_selector__default__ranking"], 11)
    assert lines == 12
    assert "(7005479104644416710," in event_text


def test_normalize_filter_user_event(rasa_normalize: Normalize) -> None:
    load_id = normalize_cases(rasa_normalize, ["event_user_load_v228_1"])
    load_files = expect_load_package(
        rasa_normalize.load_storage,
        load_id,
        ["event", "event_user", "event_user__metadata__user_nicknames",
        "event_user__parse_data__entities", "event_user__parse_data__entities__processors", "event_user__parse_data__intent_ranking"]
    )
    event_text, lines = expect_lines_file(rasa_normalize.load_storage, load_files["event_user"], 0)
    assert lines == 1
    filtered_row = json.loads(event_text)
    assert "parse_data__intent__name" in filtered_row
    # response selectors are removed
    assert_no_dict_key_starts_with(filtered_row, "parse_data__response_selector")


def test_normalize_filter_bot_event(rasa_normalize: Normalize) -> None:
    load_id = normalize_cases(rasa_normalize, ["event_bot_load_metadata_1"])
    load_files = expect_load_package(rasa_normalize.load_storage, load_id, ["event", "event_bot"])
    event_text, lines = expect_lines_file(rasa_normalize.load_storage, load_files["event_bot"], 0)
    assert lines == 1
    filtered_row = json.loads(event_text)
    assert "metadata__utter_action" in filtered_row
    assert "metadata__account_balance" not in filtered_row


def test_preserve_slot_complex_value_json_l(rasa_normalize: Normalize) -> None:
    load_id = normalize_cases(rasa_normalize, ["event_slot_session_metadata_1"])
    load_files = expect_load_package(rasa_normalize.load_storage, load_id, ["event", "event_slot"])
    event_text, lines = expect_lines_file(rasa_normalize.load_storage, load_files["event_slot"], 0)
    assert lines == 1
    filtered_row = json.loads(event_text)
    assert type(filtered_row["value"]) is str
    assert filtered_row["value"] == json.dumps({
            "user_id": "world",
            "mitter_id": "hello"
        })


def test_preserve_slot_complex_value_insert(rasa_normalize: Normalize) -> None:
    rasa_normalize.load_storage.preferred_file_format = rasa_normalize.CONFIG.LOADER_FILE_FORMAT = "insert_values"
    load_id = normalize_cases(rasa_normalize, ["event_slot_session_metadata_1"])
    load_files = expect_load_package(rasa_normalize.load_storage, load_id, ["event", "event_slot"])
    event_text, lines = expect_lines_file(rasa_normalize.load_storage, load_files["event_slot"], 2)
    assert lines == 3
    c_val = json.dumps({
            "user_id": "world",
            "mitter_id": "hello"
        })
    assert c_val in event_text


def test_normalize_raw_no_type_hints(raw_normalize: Normalize) -> None:
    normalize_event_user(raw_normalize, "event_user_load_1", EXPECTED_USER_TABLES)
    assert_timestamp_data_type(raw_normalize.load_storage, "double")


def test_normalize_raw_type_hints(rasa_normalize: Normalize) -> None:
    normalize_cases(rasa_normalize, ["event_user_load_1"])
    assert_timestamp_data_type(rasa_normalize.load_storage, "timestamp")


def test_normalize_many_events_insert(rasa_normalize: Normalize) -> None:
    rasa_normalize.load_storage.preferred_file_format = rasa_normalize.CONFIG.LOADER_FILE_FORMAT = "insert_values"
    load_id = normalize_cases(rasa_normalize, ["event_many_load_2", "event_user_load_1"])
    expected_tables = EXPECTED_USER_TABLES_RASA_NORMALIZER + ["event_bot", "event_action"]
    load_files = expect_load_package(rasa_normalize.load_storage, load_id, expected_tables)
    # return first values line from event_user file
    event_text, lines = expect_lines_file(rasa_normalize.load_storage, load_files["event"], 4)
    assert lines == 5
    assert f"'{load_id}'" in event_text


def test_normalize_many_schemas(rasa_normalize: Normalize) -> None:
    rasa_normalize.load_storage.preferred_file_format = rasa_normalize.CONFIG.LOADER_FILE_FORMAT = "insert_values"
    copy_cases(
        rasa_normalize.normalize_storage,
        ["event_many_load_2", "event_user_load_1", "ethereum_blocks_9c1d9b504ea240a482b007788d5cd61c_2"]
    )
    rasa_normalize.run(ThreadPool(processes=4))
    # must have two loading groups with model and event schemas
    loads = rasa_normalize.load_storage.list_packages()
    assert len(loads) == 2
    schemas = []
    # load all schemas
    for load_id in loads:
        schema = rasa_normalize.load_storage.load_package_schema(load_id)
        schemas.append(schema.name)
        # expect event tables
        if schema.name == "event":
            expected_tables = EXPECTED_USER_TABLES_RASA_NORMALIZER + ["event_bot", "event_action"]
            expect_load_package(rasa_normalize.load_storage, load_id, expected_tables)
        if schema.name == "ethereum":
            expect_load_package(rasa_normalize.load_storage, load_id, EXPECTED_ETH_TABLES)
    assert set(schemas) == set(["ethereum", "event"])


def test_normalize_typed_json(raw_normalize: Normalize) -> None:
    raw_normalize.load_storage.preferred_file_format = raw_normalize.CONFIG.LOADER_FILE_FORMAT = "jsonl"
    extract_items(raw_normalize.normalize_storage, [JSON_TYPED_DICT], "special")
    raw_normalize.run(ThreadPool(processes=1))
    loads = raw_normalize.load_storage.list_packages()
    assert len(loads) == 1
    # load all schemas
    schema = raw_normalize.load_storage.load_package_schema(loads[0])
    assert schema.name == "special"
    # named as schema - default fallback
    table = schema.get_table_columns("special")
    # assert inferred types
    for k, v in JSON_TYPED_DICT_TYPES.items():
        assert table[k]["data_type"] == v


EXPECTED_ETH_TABLES = ["blocks", "blocks__transactions", "blocks__transactions__logs", "blocks__transactions__logs__topics",
                       "blocks__uncles", "blocks__transactions__access_list", "blocks__transactions__access_list__storage_keys"]

EXPECTED_USER_TABLES_RASA_NORMALIZER = ["event", "event_user", "event_user__parse_data__intent_ranking"]


EXPECTED_USER_TABLES = ["event", "event__parse_data__intent_ranking", "event__parse_data__response_selector__all_retrieval_intents",
         "event__parse_data__response_selector__default__ranking", "event__parse_data__response_selector__default__response__response_templates",
         "event__parse_data__response_selector__default__response__responses"]


def extract_items(normalize_storage: NormalizeStorage, items: Sequence[StrAny], schema_name: str) -> None:
    extractor = ExtractorStorageBase("1.0.0", True, FileStorage(os.path.join(TEST_STORAGE_ROOT, "extractor"), makedirs=True), normalize_storage)
    load_id = uniq_id()
    extractor.save_json(f"{load_id}.json", items)
    extractor.commit_events(
        schema_name,
        extractor.storage.make_full_path(f"{load_id}.json"),
        "items",
        len(items),
        load_id
    )

def normalize_event_user(normalize: Normalize, case: str, expected_user_tables: List[str] = None) -> None:
    expected_user_tables = expected_user_tables or EXPECTED_USER_TABLES_RASA_NORMALIZER
    load_id = normalize_cases(normalize, [case])
    return expected_user_tables, expect_load_package(normalize.load_storage, load_id, expected_user_tables)


def normalize_cases(normalize: Normalize, cases: Sequence[str]) -> str:
    copy_cases(normalize.normalize_storage, cases)
    load_id = uniq_id()
    normalize.load_storage.create_temp_load_package(load_id)
    # pool not required for map_single
    dest_cases = [f"{NormalizeStorage.EXTRACTED_FOLDER}/{c}.extracted.json" for c in cases]
    # create schema if it does not exist
    normalize.load_or_create_schema("event")
    normalize.spool_files("event", load_id, normalize.map_single, dest_cases)
    return load_id


def copy_cases(normalize_storage: NormalizeStorage, cases: Sequence[str]) -> None:
    for case in cases:
        event_user_path = json_case_path(f"{case}.extracted")
        shutil.copy(event_user_path, normalize_storage.storage.make_full_path(NormalizeStorage.EXTRACTED_FOLDER))


def expect_load_package(load_storage: LoadStorage, load_id: str, expected_tables: Sequence[str]) -> Dict[str, str]:
    files = load_storage.list_new_jobs(load_id)
    assert len(files) == len(expected_tables)
    ofl: Dict[str, str] = {}
    for expected_table in expected_tables:
        # find all files for particular table, ignoring file id
        file_mask = load_storage.build_job_file_name(expected_table, "*", validate_components=False)
        # files are in normalized/<load_id>/new_jobs
        file_path = load_storage._get_job_file_path(load_id, "new_jobs", file_mask)
        candidates = [f for f in files if fnmatch(f, file_path)]
        assert len(candidates) == 1
        ofl[expected_table] = candidates[0]
    return ofl


def expect_lines_file(load_storage: LoadStorage, load_file: str, line: int = 0) -> str:
    with load_storage.storage.open_file(load_file) as f:
        lines = f.readlines()
    return lines[line], len(lines)


def assert_timestamp_data_type(load_storage: LoadStorage, data_type: TDataType) -> None:
    # load generated schema
    loads = load_storage.list_packages()
    event_schema = load_storage.load_package_schema(loads[0])
    # in raw normalize timestamp column must not be coerced to timestamp
    assert event_schema.get_table_columns("event")["timestamp"]["data_type"] == data_type


def test_version() -> None:
    assert normalize_configuration()._VERSION == __version__
