import soundfile as sf
import numpy as np
import os
import sys
import re

if len(sys.argv) < 2:
    print('Usage: python merge_chapters.py <folder_with_mp3_files>')
    sys.exit(1)

folder = sys.argv[1]

if not os.path.isdir(folder):
    print(f'Error: {folder} is not a valid directory.')
    sys.exit(1)

def natural_key(s):
    # Use r'(\d+)' to split on digit groups for natural sorting
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', os.path.basename(s))]

# Find all mp3 files in the folder, sorted by natural order
chapter_files = sorted([
    os.path.join(folder, f)
    for f in os.listdir(folder)
    if f.lower().endswith('.mp3')
], key=natural_key)

print('Order of files to be merged:')
for f in chapter_files:
    print(f)

if not chapter_files:
    print(f'No mp3 files found in folder: {folder}')
    exit(1)

all_samples = []
sample_rate = None

for chapter_file in chapter_files:
    print(f'Processing {chapter_file}')
    data, sr = sf.read(chapter_file)
    if sample_rate is None:
        sample_rate = sr
    elif sr != sample_rate:
        print(f"Warning: Sample rate mismatch in {chapter_file}, skipping.")
        continue
    all_samples.append(data)

if all_samples:
    merged = np.concatenate(all_samples)
    sf.write('merged_book.mp3', merged, sample_rate)
    print('Merged file saved as merged_book.mp3')
else:
    print('No audio data to merge.')
