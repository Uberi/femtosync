#!/usr/bin/env bash

# set up bash to handle errors more aggressively - a "strict mode" of sorts
set -e # give an error if any command finishes with a non-zero exit code
set -u # give an error if we reference unset variables
set -o pipefail # for a pipeline, if any of the commands fail with a non-zero exit code, fail the entire pipeline with that exit code

# set up some sample data directories
rm -rf test-src test-dest
mkdir -p test-src/dir-dir test-src/dir-file test-src/dir-nothing test-dest/dir-dir test-dest/file-dir test-dest/nothing-dir
head -c 20M < /dev/urandom > test-src/file-nothing
head -c 20M < /dev/urandom > test-src/file-file-mismatch
head -c 20M < /dev/urandom > test-src/file-file-mismatch-permission-error
head -c 20M < /dev/urandom > test-src/file-file-full-match
head -c 20M < /dev/urandom > test-src/file-file-suffix-match
head -c 20M < /dev/urandom > test-src/file-dir
head -c 20M < /dev/urandom > test-dest/nothing-file
head -c 20M < /dev/urandom > test-dest/file-file-mismatch
head -c 20M < /dev/urandom > test-dest/file-file-mismatch-permission-error; chmod 000 test-dest/file-file-mismatch-permission-error
cp test-src/file-file-full-match test-dest/file-file-full-match
head -c 5M < /dev/urandom > test-dest/file-file-suffix-match; cat test-src/file-file-suffix-match >> test-dest/file-file-suffix-match
head -c 20M < /dev/urandom > test-dest/dir-file

function cleanup {
    rm -rf test-src test-dest 2> /dev/null || true
    kill $RECEIVER_PID 2> /dev/null || true
}
RECEIVER_PID=
trap cleanup EXIT

# run the scripts
./femtosync-receiver.py test-dest &
RECEIVER_PID=$!
./femtosync-sender.py test-src
kill $RECEIVER_PID
echo '============= DIFFERENCES IN SOURCE AND DESTINATION (SHOULD JUST BE ENTRIES WITH "-error" SUFFIX) ============='
diff -qr test-src test-dest || true
echo '==============================================================================================================='
( ! diff -qr test-src test-dest | grep -v '-error' )  # will fail if any of the diff output lines don't contain "-error"

# set up some sample data files
rm -rf test-src test-dest
head -c 20M < /dev/urandom > test-src
head -c 20M < /dev/urandom > test-dest

# run the scripts
./femtosync-receiver.py test-dest &
RECEIVER_PID=$!
./femtosync-sender.py test-src
kill $RECEIVER_PID
echo '============= DIFFERENCES IN SOURCE AND DESTINATION (SHOULD BE EMPTY) ============='
diff -qr test-src test-dest  # will fail if there are differences
echo '==================================================================================='

# set up some sample data files
rm -rf test-src test-dest
mkdir -p test-src/dir-nothing
head -c 20M < /dev/urandom > test-src/file-nothing
head -c 20M < /dev/urandom > test-dest

# run the scripts
./femtosync-receiver.py test-dest &
RECEIVER_PID=$!
./femtosync-sender.py test-src
kill $RECEIVER_PID
echo '============= DIFFERENCES IN SOURCE AND DESTINATION (SHOULD BE EMPTY) ============='
diff -qr test-src test-dest  # will fail if there are differences
echo '==================================================================================='

echo '============================================'
echo '============= ALL TESTS PASSED ============='
echo '============================================'
