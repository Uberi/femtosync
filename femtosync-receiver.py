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
parser.add_argument("target", help="the file or directory to download to", default=".")
args = parser.parse_args()

SYNC_TARGET = args.target  # note that in the Pyto app on iOS, this setting can be dynamically changed by making a POST request to /ios_set_target_directory
SERVER_HOST = "0.0.0.0" if args.public else "127.0.0.1"
SERVER_PORT = args.port
ROLLING_WINDOW_SIZE = 0x100000  # 1 MiB


def validate_path_is_trustworthy(untrusted_relative_path, trusted_directory_or_file):
    normalized_directory_or_file = os.path.abspath(trusted_directory_or_file)
    normalized_path = os.path.normpath(os.path.join(normalized_directory_or_file, untrusted_relative_path))
    assert os.path.commonpath([normalized_path, normalized_directory_or_file]) == normalized_directory_or_file, "Untrusted relative path must be inside trusted directory or equal to trusted file"
    return normalized_path


def recursive_list(current_path, file_identifier_func):
    if os.path.isdir(current_path):
        with os.scandir(current_path) as it:
            return {entry.name: recursive_list(entry.path, file_identifier_func) for entry in it}
    else:
        return file_identifier_func(current_path)


def makedirs_force(path, mode=0o777):
    """Like os.makedirs but will also delete files that conflict with any parts of this path along the way."""
    if os.path.isfile(path):
        os.remove(path)
    if not os.path.exists(path):
        if path != os.path.dirname(path):  # not already at top-level directory
            makedirs_force(os.path.dirname(path), mode)
        os.mkdir(path, mode)


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
    global SYNC_TARGET
    SYNC_TARGET = file_system.pick_directory()
    return dict(status="success")


class SyncRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/directory_tree_size_and_mtime":
            def size_and_mtime_identifier(path):
                stat_result = os.stat(path)
                return [os.path.basename(path), stat_result.st_size, stat_result.st_mtime_ns]
            self.respond_json(dict(status="success", result=recursive_list(SYNC_TARGET, size_and_mtime_identifier)))
        if self.path == "/directory_tree_checksum":
            def checksum_identifier(path):
                with open(path, "rb") as f:
                    return [os.path.basename(path), hashlib.sha256(f.read()).hexdigest()]
            self.respond_json(dict(status="success", result=recursive_list(SYNC_TARGET, checksum_identifier)))
        elif self.path.startswith("/block_checksums/"):
            normalized_path = validate_path_is_trustworthy(urllib.parse.unquote(self.path[len("/block_checksums/"):]), SYNC_TARGET)
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
            normalized_path = validate_path_is_trustworthy(urllib.parse.unquote(self.path[len("/create_directory/"):]), SYNC_TARGET)
            print("creating directory:", normalized_path)
            makedirs_force(normalized_path)
            self.respond_json(dict(status="success"))
        elif self.path.startswith("/create_or_append_file/"):
            normalized_path = validate_path_is_trustworthy(urllib.parse.unquote(self.path[len("/create_or_append_file/"):]), SYNC_TARGET)
            print("creating or appending to file:", normalized_path)
            if os.path.isdir(normalized_path):  # if this is a directory, delete it first
                shutil.rmtree(normalized_path)
            file_contents = read_file_bytes(self.rfile, int(self.headers['content-length']))
            with open(normalized_path, "ab") as f:
                f.write(file_contents)
            self.respond_json(dict(status="success"))
        elif self.path.startswith("/finish_create_file/"):
            normalized_path = validate_path_is_trustworthy(urllib.parse.unquote(self.path[len("/finish_create_file/"):]), SYNC_TARGET)
            print("updating file mtime:", normalized_path)
            modified_time = int(read_file_bytes(self.rfile, int(self.headers['content-length'])), 10)
            os.utime(normalized_path, ns=(modified_time, modified_time))
            self.respond_json(dict(status="success"))
        elif self.path.startswith("/create_or_append_patch/"):
            target_patch_file, target_path = self.path[len("/create_or_append_patch/"):].split("/", maxsplit=1)
            normalized_path = validate_path_is_trustworthy(urllib.parse.unquote(target_path), SYNC_TARGET)
            target_patch_path = os.path.join(os.path.dirname(normalized_path), os.path.basename(target_patch_file))
            print("creating or appending patched file:", normalized_path, target_patch_path)
            file_patch = read_file_bytes(self.rfile, int(self.headers['content-length']))

            # generate the new file and overwrite the old file once done
            with open(normalized_path, "rb") as old_f, open(target_patch_path, "ab") as new_f:
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
            target_patch_file, target_path = self.path[len("/finish_patch/"):].split("/", maxsplit=1)
            normalized_path = validate_path_is_trustworthy(urllib.parse.unquote(target_path), SYNC_TARGET)
            target_patch_path = os.path.join(os.path.dirname(normalized_path), os.path.basename(target_patch_file))
            print("completing patched file:", normalized_path, target_patch_path)
            modified_time = int(read_file_bytes(self.rfile, int(self.headers['content-length'])), 10)
            os.rename(target_patch_path, normalized_path)
            os.utime(normalized_path, ns=(modified_time, modified_time))
            self.respond_json(dict(status="success"))
        elif self.path.startswith("/delete_file_or_directory/"):
            normalized_path = validate_path_is_trustworthy(urllib.parse.unquote(self.path[len("/delete_file_or_directory/"):]), SYNC_TARGET)
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
        # connect() on a UDP socket doesn't actually send any packets, so the IP address doesn't actually have to be reachable at all
        # however, note that this can fail if the computer isn't connected to any networks, because in that case we simply don't have a local IP address
        s.connect(('8.8.8.8', 1))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


if __name__ == '__main__':
    print(f"Starting server listening at {SERVER_HOST}:{SERVER_PORT}")
    local_ip_address = get_local_ip_address()
    print(f"On the sending computer, run this command: ./femtosync-sender.py --host {'INSERT_LOCAL_IP_ADDRESS_HERE' if local_ip_address is None else local_ip_address} --port {SERVER_PORT} INSERT_SYNC_SOURCE_FILE_OR_DIRECTORY")

    server = HTTPServer((SERVER_HOST, SERVER_PORT), SyncRequestHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
