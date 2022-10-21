#!/usr/bin/env python3

import json
import urllib.request
import os
import hashlib
import struct
import argparse
import collections

parser = argparse.ArgumentParser()
parser.add_argument("--host", help="host that the server is listening on", default="127.0.0.1")
parser.add_argument("--port", help="network port that the server is listening on", type=int, default=5000)
parser.add_argument("--files-dir", help="directory to upload/download files from (prefix with @ to specify that the path is relative to the Femtosync executable)", default=".")
parser.add_argument("--checksum", help="if this flag is specified, file comparison will be performed using full checksumming rather than just comparing size and last-modified-time", action="store_true")
parser.add_argument("--dry-run", help="just print out what actions would be performed, without actually performing them", action="store_true")
parser.add_argument("--ios-select-directory", help="if the receiver is running in the Pyto app on iOS, trigger an iOS prompt to select the upload/download directory (this allows the server to sync files in other apps' containers on iOS, e.g. Flacbox music)", action="store_true")
args = parser.parse_args()

if args.files_dir.startswith("@"):
    SYNC_SOURCE_DIRECTORY = os.path.join(os.path.dirname(os.path.realpath(__file__)), args.files_dir[1:])
else:
    SYNC_SOURCE_DIRECTORY = args.files_dir

SERVER_HOST_AND_PORT = f"{args.host}:{args.port}"
ROLLING_WINDOW_SIZE = 0x100000  # 1 MiB, the size of the rsync rolling checksum window
MAX_CHUNK_SIZE = 0x1000000  # 16 MiB, the maximum size of a request body sent over the network (also determines maximum memory usage at the receiver)


def api_call(endpoint_path, post_data=None):
    response = urllib.request.urlopen(f"http://{SERVER_HOST_AND_PORT}{endpoint_path}", post_data)
    response_content = json.loads(response.read().decode("utf-8"))
    assert response_content["status"] == "success", response_content.get("result")
    return response_content.get("result")


def recursive_diff(source_path, destination_tree, original_source_path, file_identifier_func):
    leftover_destination_entries = set(destination_tree.keys())
    with os.scandir(source_path) as it:
        for entry in it:
            if entry.name in leftover_destination_entries:
                leftover_destination_entries.remove(entry.name)
            if entry.is_dir():  # visit subdirectory next
                if not isinstance(destination_tree.get(entry.name), dict):  # this directory doesn't match a directory with the same name at the destination
                    yield ('create_directory', os.path.relpath(entry.path, start=original_source_path))
                yield from recursive_diff(entry.path, destination_tree.get(entry.name, {}), original_source_path, file_identifier_func)
            else:  # process this file
                if not isinstance(destination_tree.get(entry.name), list):  # this file doesn't match a file with the same name at the destination
                    yield ('create_file', os.path.relpath(entry.path, start=original_source_path))
                elif file_identifier_func(entry) != destination_tree[entry.name]:  # this file doesn't match the file with the same name at the destination
                    path = os.path.relpath(entry.path, start=original_source_path)
                    i = 0
                    while f"{path}.tmp.{i}" in destination_tree:  # find an unused path where we can create the patched version of the file
                        i += 1
                    yield ('patch_file', (path, f".tmp.{i}"))
    for entry_name in leftover_destination_entries:  # extraneous files that should be removed from the destination
        yield ('delete', os.path.relpath(os.path.join(source_path, entry_name), start=original_source_path))


def read_file_bytes(f, size):
    """The `read(size)` method on a file-like object can return less than `size` bytes. This can happen if using non-blocking mode or a non-CPython version of Python."""
    result = bytearray()
    while len(result) < size:
        buffer = f.read(size - len(result))
        if not buffer:
            break
        result += buffer
    return bytes(result)


def generate_file_patch(source_file, destination_block_checksums):
    destination_rollable_checksums, destination_collision_resistant_checksums = destination_block_checksums
    destination_rollable_checksums_map = collections.defaultdict(list)
    for i, checksum in enumerate(destination_rollable_checksums):
        destination_rollable_checksums_map[checksum].append(i)

    # initialize the rolling window and rolling checksum
    source_rolling_window = collections.deque(read_file_bytes(source_file, ROLLING_WINDOW_SIZE))
    source_rolling_checksum_a, source_rolling_checksum_b = sum(source_rolling_window), sum((len(source_rolling_window) - i) * d for i, d in enumerate(source_rolling_window))
    source_rolling_checksum = (source_rolling_checksum_b << 16) | source_rolling_checksum_a

    literal_data_buffer = bytearray()
    latest_matched_block_index = -1
    reached_end_of_source_file = False
    while source_rolling_window:
        # check for potential block matches using the rolling checksum, then verify those potential matches using the collision-resistant checksum
        if source_rolling_checksum in destination_rollable_checksums_map:
            source_collision_resistant_checksum = hashlib.sha256(bytes(source_rolling_window)).hexdigest()
            matched_block_index = next((i for i in destination_rollable_checksums_map[source_rolling_checksum] if destination_collision_resistant_checksums[i] == source_collision_resistant_checksum), -1)
        else:
            matched_block_index = -1

        if matched_block_index != -1:
            # match found, output the index of the destination file block that matched
            latest_matched_block_index = matched_block_index
            if literal_data_buffer:  # flush the literal data buffer
                yield bytes(literal_data_buffer)
                literal_data_buffer.clear()
            yield matched_block_index

            # re-initialize the rolling window and rolling checksum to right after the matched block
            source_rolling_window.clear()
            source_rolling_window.extend(read_file_bytes(source_file, ROLLING_WINDOW_SIZE))
            source_rolling_checksum_a, source_rolling_checksum_b = sum(source_rolling_window), sum((len(source_rolling_window) - i) * d for i, d in enumerate(source_rolling_window))
            source_rolling_checksum = (source_rolling_checksum_b << 16) | source_rolling_checksum_a
        else:  # no match found, move to the next byte
            # roll the window forward by one byte
            old_byte = source_rolling_window.popleft()
            if reached_end_of_source_file:
                new_byte = 0
            else:
                source_byte = source_file.read(1)
                if source_byte:  # reached end of source file
                    new_byte = source_byte[0]
                    source_rolling_window.append(new_byte)
                else:
                    new_byte = 0
                    reached_end_of_source_file = True

            # roll the window forward by one byte, calculate the new rolling checksum, add the old byte to the current literal data buffer
            source_rolling_checksum_a -= old_byte - new_byte
            source_rolling_checksum_b -= old_byte * ROLLING_WINDOW_SIZE - source_rolling_checksum_a
            source_rolling_checksum = (source_rolling_checksum_b << 16) | source_rolling_checksum_a
            literal_data_buffer.append(old_byte)
    if literal_data_buffer:
        yield bytes(literal_data_buffer)  # flush the literal data buffer


def chunk_file_patch(file_patch, max_chunk_size):
    current_chunk = bytearray()
    for block_number_or_data in file_patch:
        if isinstance(block_number_or_data, int):  # block number
            if max_chunk_size - len(current_chunk) < 8:  # not enough room left for the block number, flush the current chunk
                yield bytes(current_chunk)
                current_chunk.clear()
            current_chunk += struct.pack("<q", -block_number_or_data)
        else:  # literal data buffer
            position = 0
            while True:  # break up the literal data buffer so that it fits into the chunks
                if max_chunk_size - len(current_chunk) < 8 + 1:  # not enough room left for another literal data buffer slice, flush the current chunk
                    yield bytes(current_chunk)
                    current_chunk.clear()
                data_slice = block_number_or_data[position:position + (max_chunk_size - len(current_chunk) - 8)]
                if not data_slice:
                    break
                current_chunk += struct.pack("<q", len(data_slice)) + data_slice
                position += len(data_slice)
    if current_chunk:
        yield bytes(current_chunk)


def size_and_mtime_identifier(entry: os.DirEntry):
    stat_result = entry.stat()
    return [entry.name, stat_result.st_size, stat_result.st_mtime_ns]


def checksum_identifier(entry: os.DirEntry):
    with open(entry.path, "rb") as f:
        return [entry.name, hashlib.sha256(f.read()).hexdigest()]


if args.ios_select_directory:
    api_call(f"/ios_select_directory", b"")

if args.checksum:
    file_identifier_func = checksum_identifier
    destination_tree = api_call("/directory_tree_checksum")
else:
    file_identifier_func = size_and_mtime_identifier
    destination_tree = api_call("/directory_tree_size_and_mtime")

create_directory_paths, create_file_paths, patch_file_paths, delete_paths = [], [], [], []
for action, path in recursive_diff(SYNC_SOURCE_DIRECTORY, destination_tree, SYNC_SOURCE_DIRECTORY, file_identifier_func):
    if action == "create_directory":
        create_directory_paths.append(path)
    elif action == "create_file":
        create_file_paths.append(path)
    elif action == "patch_file":
        patch_file_paths.append(path)
    elif action == "delete":
        delete_paths.append(path)
    else:
        assert False, f"Invalid action: {action}"

for i, path in enumerate(delete_paths):
    print(f'deleting {i + 1} of {len(delete_paths)}:', path)
    if not args.dry_run:
        api_call(f"/delete_file_or_directory/{urllib.parse.quote(path)}", b"")
for i, path in enumerate(create_directory_paths):
    print(f'creating directory {i + 1} of {len(create_directory_paths)}:', path)
    if not args.dry_run:
        api_call(f"/create_directory/{urllib.parse.quote(path)}", b"")
for i, path in enumerate(create_file_paths):
    print(f'creating file {i + 1} of {len(create_file_paths)}:', path)
    source_path = os.path.join(SYNC_SOURCE_DIRECTORY, path)
    modified_time = os.stat(source_path).st_mtime_ns
    if not args.dry_run:
        with open(source_path, "rb") as f:
            while True:
                buffer = f.read(MAX_CHUNK_SIZE)
                if not buffer:
                    break
                print(f'-> uploading {len(buffer)} byte chunk...')
                api_call(f"/create_or_append_file/{urllib.parse.quote(path)}", buffer)
        api_call(f"/update_file_mtime/{urllib.parse.quote(path)}", str(modified_time).encode("ascii"))
for i, (path, suffix) in enumerate(patch_file_paths):
    print(f'patching file {i + 1} of {len(patch_file_paths)}:', path)
    source_path = os.path.join(SYNC_SOURCE_DIRECTORY, path)
    modified_time = os.stat(source_path).st_mtime_ns
    if not args.dry_run:
        destination_block_checksums = api_call(f"/block_checksums/{urllib.parse.quote(path)}")
        with open(source_path, "rb") as f:
            for file_patch_chunk in chunk_file_patch(generate_file_patch(f, destination_block_checksums), MAX_CHUNK_SIZE):
                api_call(f"/create_or_append_patch/{suffix}/{urllib.parse.quote(path)}", file_patch_chunk)
        api_call(f"/finish_patch/{suffix}/{urllib.parse.quote(path)}", str(modified_time).encode("ascii"))
