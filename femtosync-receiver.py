#!/usr/bin/env python3

import json
import os
import shutil
import hashlib
import struct
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--port", help="local network port to listen on", type=int, default=5000)
parser.add_argument("--public", help="listen on remote network interfaces (allows other hosts to see the website; otherwise only this host can see it)", action="store_true")
parser.add_argument("--files-dir", help="directory to upload/download files from (prefix with @ to specify that the path is relative to the Femtosync executable)", default=".")
args = parser.parse_args()

# note that in the Pyto app on iOS, this setting can be dynamically changed by making a POST request to /ios_set_target_directory
if args.files_dir.startswith("@"):
    SYNC_TARGET_DIRECTORY = os.path.join(os.path.dirname(os.path.realpath(__file__)), args.files_dir[1:])
else:
    SYNC_TARGET_DIRECTORY = args.files_dir

SERVER_HOST = "0.0.0.0" if args.public else "127.0.0.1"
SERVER_PORT = args.port
ROLLING_WINDOW_SIZE = 0x100000  # 1 MiB


def check_path_inside_directory(untrusted_relative_path, trusted_directory):
    normalized_directory = os.path.abspath(trusted_directory)
    normalized_path = os.path.normpath(os.path.join(normalized_directory, untrusted_relative_path))
    assert os.path.commonpath([normalized_path, normalized_directory]) == normalized_directory, "Untrusted relative path must be inside trusted directory"
    return normalized_path


def recursive_list(current_path, file_identifier_func):
    result = {}
    with os.scandir(current_path) as it:
        for entry in it:
            if entry.is_dir():  # visit subdirectory next
                result[entry.name] = recursive_list(entry.path, file_identifier_func)
            else:  # process this file
                result[entry.name] = file_identifier_func(entry)
    return result


def makedirs_force(name, mode=0o777):
    """Like os.makedirs but will also delete files that conflict with any parts of this path along the way."""
    head, tail = os.path.split(name)
    if not tail:
        head, tail = os.path.split(head)
    if head and tail and not os.path.isdir(head):
        if os.path.exists(head):  # head is a file, remove it to make way for this directory
            os.remove(head)
        makedirs_force(head, mode)
        if tail == curdir:  # directory exists, we're done
            return
    try:
        os.mkdir(name, mode)
    except OSError:
        if not os.path.isdir(name):  # we didn't end up actually creating the directory
            raise


def read_file_bytes(f, size):
    """The `read(size)` method on a file-like object can return less than `size` bytes. This can happen if using non-blocking mode or a non-CPython version of Python."""
    result = bytearray()
    while len(result) < size:
        buffer = f.read(size - len(result))
        if not buffer:
            break
        result += buffer
    return bytes(result)


def handle_post_ios_select_directory():
    # this is a library provided by the Pyto app in iOS, see https://github.com/ColdGrub1384/Pyto/blob/4b7ef328362fe1e86a12960da7ce4c102ee02beb/docs/external.rst
    try:
        import file_system
    except ImportError:
        return dict(status="error", result="This server isn't running in the Pyto app in iOS")

    # shows directory selection modal on iOS, then
    # gives us the necessary permissions to actually read from or write to the selected directory, then
    # returns the selected directory as a path string
    global SYNC_TARGET_DIRECTORY
    SYNC_TARGET_DIRECTORY = file_system.pick_directory()
    return dict(status="success")


class SyncRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/directory_tree_size_and_mtime":
            def size_and_mtime_identifier(entry: os.DirEntry):
                stat_result = entry.stat()
                return [entry.name, stat_result.st_size, stat_result.st_mtime_ns]
            self.respond_json(dict(status="success", result=recursive_list(SYNC_TARGET_DIRECTORY, size_and_mtime_identifier)))
        if self.path == "/directory_tree_checksum":
            def checksum_identifier(entry: os.DirEntry):
                with open(entry.path, "rb") as f:
                    return [entry.name, hashlib.sha256(f.read()).hexdigest()]
            self.respond_json(dict(status="success", result=recursive_list(SYNC_TARGET_DIRECTORY, checksum_identifier)))
        elif self.path.startswith("/block_checksums/"):
            normalized_path = check_path_inside_directory(urllib.parse.unquote(self.path[len("/block_checksums/"):]), SYNC_TARGET_DIRECTORY)
            rollable_checksums, collision_resistant_checksums = [], []
            if os.path.exists(normalized_path):
                with open(normalized_path, "rb") as f:
                    while True:
                        buffer = read_file_bytes(f, ROLLING_WINDOW_SIZE)
                        if not buffer:
                            break
                        a, b = sum(buffer), sum((len(buffer) - i) * d for i, d in enumerate(buffer))
                        rollable_checksums.append((b << 16) | a)
                        collision_resistant_checksums.append(hashlib.sha256(buffer).hexdigest())
            self.respond_json(dict(status="success", result=[rollable_checksums, collision_resistant_checksums]))
        else:
            self.respond_json(dict(status="error", result="Endpoint doesn't exist"), status_code=404)

    def do_POST(self):
        if self.path == "/ios_select_directory":
            self.respond_json(handle_post_ios_select_directory())
        elif self.path.startswith("/create_directory/"):
            normalized_path = check_path_inside_directory(urllib.parse.unquote(self.path[len("/create_directory/"):]), SYNC_TARGET_DIRECTORY)
            print("creating directory:", normalized_path)
            makedirs_force(normalized_path)
            self.respond_json(dict(status="success"))
        elif self.path.startswith("/create_or_append_file/"):
            normalized_path = check_path_inside_directory(urllib.parse.unquote(self.path[len("/create_or_append_file/"):]), SYNC_TARGET_DIRECTORY)
            print("creating or appending to file:", normalized_path)
            if os.path.isdir(normalized_path):  # if this is a directory, delete it first
                shutil.rmtree(normalized_path)
            file_contents = read_file_bytes(self.rfile, int(self.headers['content-length']))
            with open(normalized_path, "ab") as f:
                f.write(file_contents)
            self.respond_json(dict(status="success"))
        elif self.path.startswith("/create_or_append_patch/"):
            patched_file_suffix, target_path = self.path[len("/create_or_append_patch/"):].split("/", maxsplit=1)
            normalized_path = check_path_inside_directory(urllib.parse.unquote(target_path), SYNC_TARGET_DIRECTORY)
            print("creating or appending patched file:", normalized_path + patched_file_suffix)
            file_patch = read_file_bytes(self.rfile, int(self.headers['content-length']))

            # generate the new file and overwrite the old file once done
            with open(normalized_path, "rb") as old_f, open(normalized_path + patched_file_suffix, "ab") as new_f:
                patch_position = 0
                while patch_position < len(file_patch):
                    block_number_or_data_size = struct.unpack("<q", file_patch[patch_position:patch_position + 8])[0]
                    patch_position += 8
                    if block_number_or_data_size <= 0:  # block number, read that block from our version of the file and write it
                        old_f.seek(abs(block_number_or_data_size) * ROLLING_WINDOW_SIZE)
                        new_f.write(read_file_bytes(old_f, ROLLING_WINDOW_SIZE))
                    else:  # raw data, write it
                        new_f.write(file_patch[patch_position:patch_position + block_number_or_data_size])
                        patch_position += block_number_or_data_size
            self.respond_json(dict(status="success"))
        elif self.path.startswith("/finish_patch/"):
            patched_file_suffix, target_path = self.path[len("/finish_patch/"):].split("/", maxsplit=1)
            normalized_path = check_path_inside_directory(urllib.parse.unquote(target_path), SYNC_TARGET_DIRECTORY)
            print("completing patched file:", normalized_path)
            modified_time = int(read_file_bytes(self.rfile, int(self.headers['content-length'])), 10)
            os.rename(normalized_path + patched_file_suffix, normalized_path)
            os.utime(normalized_path, ns=(modified_time, modified_time))
            self.respond_json(dict(status="success"))
        elif self.path.startswith("/update_file_mtime/"):
            normalized_path = check_path_inside_directory(urllib.parse.unquote(self.path[len("/update_file_mtime/"):]), SYNC_TARGET_DIRECTORY)
            print("updating file mtime:", normalized_path)
            modified_time = int(read_file_bytes(self.rfile, int(self.headers['content-length'])), 10)
            os.utime(normalized_path, ns=(modified_time, modified_time))
            self.respond_json(dict(status="success"))
        elif self.path.startswith("/delete_file_or_directory/"):
            normalized_path = check_path_inside_directory(urllib.parse.unquote(self.path[len("/delete_file_or_directory/"):]), SYNC_TARGET_DIRECTORY)
            print("deleting:", normalized_path)
            try:
                os.remove(normalized_path)
            except IsADirectoryError:
                shutil.rmtree(normalized_path)
            self.respond_json(dict(status="success"))
        else:
            self.respond_json(dict(status="error", result="Endpoint doesn't exist"), status_code=404)

    def respond_json(self, response, status_code=200):
        response_bytes = json.dumps(response).encode("utf8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.end_headers()
        self.wfile.write(response_bytes)


def get_local_ip_address():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 1))  # connect() on a UDP socket doesn't actually send any packets, so the IP address doesn't actually have to be reachable at all
        return s.getsockname()[0]
    finally:
        s.close()


if __name__ == '__main__':
    print(f"Starting server listening at {SERVER_HOST}:{SERVER_PORT}")
    print(f"On the sending computer, run this command: femtosync-sender.py --host {get_local_ip_address()} --port {SERVER_PORT} --files-dir SOME_SYNC_SOURCE_DIRECTORY")

    server = HTTPServer((SERVER_HOST, SERVER_PORT), SyncRequestHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
