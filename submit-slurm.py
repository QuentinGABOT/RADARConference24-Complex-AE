#!/usr/bin/python

import os
import sys
import subprocess
import tempfile


def makejob(commit_id, configpath, nruns):
    return f"""#!/bin/bash

#SBATCH --job-name=monjob
#SBATCH --nodes=1
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=10
#SBATCH --mem=200G
#SBATCH --tmp=30G
#SBATCH --partition=gpu_test
#SBATCH --time=00:10:00
#SBATCH --output=logslurms/slurm-%j.out
#SBATCH --error=logslurms/slurm-%j.err

current_dir=`pwd`
export PATH=$PATH:~/.local/bin

echo "Session " ${{SLURM_ARRAY_JOB_ID}}_${{SLURM_ARRAY_TASK_ID}}

echo "Running on " $(hostname)

echo "Copying the source directory and data"
date
mkdir $TMPDIR/code
rsync -r --exclude logs --exclude logslurms --exclude configs . $TMPDIR/code

echo "Checking out the correct version of the code commit_id {commit_id}"
cd $TMPDIR/code
git checkout {commit_id}


echo "Setting up the virtual environment"
python3 -m pip install virtualenv --user
virtualenv -p python3 venv
source venv/bin/activate

# Install the library
python -m pip install .

echo "Training"
python -m torchtmpl.main {configpath} train

if [[ $? != 0 ]]; then
    exit -1
fi
"""


def submit_job(job):
    with open("job.sbatch", "w") as fp:
        fp.write(job)
    os.system("sbatch job.sbatch")


# Ensure all the modified files have been staged and commited
# This is to guarantee that the commit id is a reliable certificate
# of the version of the code you want to evaluate
result = int(
    subprocess.run(
        "expr $(git diff --name-only | wc -l) + $(git diff --name-only --cached | wc -l)",
        shell=True,
        stdout=subprocess.PIPE,
    ).stdout.decode()
)
if result > 0:
    print(f"We found {result} modifications either not staged or not commited")
    raise RuntimeError(
        "You must stage and commit every modification before submission "
    )

commit_id = subprocess.check_output(
    "git log --pretty=format:'%H' -n 1", shell=True
).decode()

print(f"I will be using the commit id {commit_id}")

# Ensure the log directory exists
os.system("mkdir -p logslurms")

if len(sys.argv) not in [2, 3]:
    print(f"Usage : {sys.argv[0]} config.yaml <nruns|1>")
    sys.exit(-1)

configpath = sys.argv[1]
if len(sys.argv) == 2:
    nruns = 1
else:
    nruns = int(sys.argv[2])

# Copy the config in a temporary config file
os.system("mkdir -p configs")
tmp_configfilepath = tempfile.mkstemp(dir="./configs", suffix="-config.yml")[1]
os.system(f"cp {configpath} {tmp_configfilepath}")

# Launch the batch jobs
submit_job(makejob(commit_id, tmp_configfilepath, nruns))
