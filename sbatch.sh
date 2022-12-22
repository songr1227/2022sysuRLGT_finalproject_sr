#!/bin/bash
# Ask SLURM to send the USR1 signal 300 seconds before end of the time limit
#SBATCH --signal=B:USR1@300
#SBATCH --output=output/%x/%a.txt
#SBATCH --mail-type=ALL
#SBATCH --mail-type=TIME_LIMIT
#SBATCH --exclude=nc20552,nc11103,nc11126,nc10303,nc20305,nc10249,nc20325,nc11124,nc20529,nc20526,nc20342,nc20354,nc30616,nc30305,nc20133,nc10220
# ---------------------------------------------------------------------
echo "Current working directory: `pwd`"
echo "Starting run at: `date`"
# ---------------------------------------------------------------------
echo "Job Array ID / Job ID: $SLURM_ARRAY_JOB_ID / $SLURM_JOB_ID"
echo "This is job $SLURM_ARRAY_TASK_ID out of $SLURM_ARRAY_TASK_COUNT jobs"
echo "SLURM_TMPDIR: $SLURM_TMPDIR"
echo "SLURM_JOB_NODELIST: $SLURM_JOB_NODELIST"
# ---------------------------------------------------------------------
cleanup()
{
    echo "Copy log files from temporary directory"
    sour=$SLURM_TMPDIR/$SLURM_JOB_NAME/$SLURM_ARRAY_TASK_ID/
    dest=./logs/$SLURM_JOB_NAME/
    echo "Source directory: $sour"
    echo "Destination directory: $dest"
    cp -rf $sour $dest
}
# Call `cleanup` once we receive USR1 or EXIT signal
trap 'cleanup' USR1 EXIT
# ---------------------------------------------------------------------
export OMP_NUM_THREADS=1
module load gcc/9.3.0 arrow/2.0.0 python/3.7 scipy-stack
source ~/envs/gym/bin/activate
python main.py --config_file ./configs/${SLURM_JOB_NAME}.json --config_idx $SLURM_ARRAY_TASK_ID --slurm_dir $SLURM_TMPDIR
# python main.py --config_file ./configs/${SLURM_JOB_NAME}.json --config_idx $SLURM_ARRAY_TASK_ID
# ---------------------------------------------------------------------
echo "Job finished with exit code $? at: `date`"
# ---------------------------------------------------------------------