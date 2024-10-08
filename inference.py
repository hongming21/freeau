import os
import copy
import json
import time
import torch
import argparse
import soundfile as sf
from tqdm import tqdm
from diffusers import DDPMScheduler
from audioldm_eval import EvaluationHelper
from models import build_pretrained_models, AudioDiffusion
from transformers import AutoProcessor, ClapModel
import torchaudio
from tango import Tango

        
def parse_args():
    parser = argparse.ArgumentParser(description="Inference for text to audio generation task.")
    parser.add_argument(
        "--checkpoint", type=str, default="declare-lab/tango",
        help="Tango huggingface checkpoint"
    )
    parser.add_argument(
        "--test_file", type=str, default="data/test_audiocaps_subset.json",
        help="json file containing the test prompts for generation."
    )
    parser.add_argument(
        "--text_key", type=str, default="captions",
        help="Key containing the text in the json file."
    )
    parser.add_argument(
        "--device", type=str, default="cuda:0",
        help="Device to use for inference."
    )
    parser.add_argument(
        "--num_steps", type=int, default=200,
        help="How many denoising steps for generation.",
    )
    parser.add_argument(
        "--guidance", type=float, default=3,
        help="Guidance scale for classifier free guidance."
    )
    parser.add_argument(
        "--batch_size", type=int, default=8,
        help="Batch size for generation.",
    )
    parser.add_argument(
        "--logdir", type=str, default="./output",
        help="Batch size for generation.",
    )
    parser.add_argument(
        "--test_references", type=str, default="data/audiocaps_test_references/subset",
        help="Folder containing the test reference wav files."
    )
    parser.add_argument(
        "--adjust_mode", type=str, default="none",
        help="param adjust mode"
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="seed everything",
    )
    parser.add_argument(
        "--s1", type=float, default=1.0,
        help="s1",
    )
    parser.add_argument(
        "--s2", type=float, default=1.0,
        help="s2",
    )
    parser.add_argument(
        "--b1", type=float, default=1.0,
        help="b1",
    )
    parser.add_argument(
        "--b2", type=float, default=1.0,
        help="b2",
    )
    parser.add_argument(
        "--m", type=float, default=1.0,
        help="m",
    )

    args = parser.parse_args()

    return args
def seed_everything(seed):
    import random, os
    import numpy as np
    import torch

    random.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = True

def main():
    args = parse_args()
    num_steps, guidance, batch_size = args.num_steps, args.guidance, args.batch_size
    checkpoint = args.checkpoint
    logdir = args.logdir
    seed = args.seed
    seed_everything(seed)
    bs_param = (args.s1, args.s2, args.b1, args.b2)
    m=args.m
    schedule_mode = args.adjust_mode
    model_name = checkpoint.split('/')[-1]

    # Load Models #
    tango = Tango(checkpoint, args.device)
    vae, stft, model = tango.vae, tango.stft, tango.model
    scheduler = DDPMScheduler.from_pretrained("stabilityai/stable-diffusion-2-1", subfolder="scheduler")
    text_prompts = []
    filenames = []

    # Load Data #
    for line in open(args.test_file).readlines():
        data = json.loads(line)
        text_prompts.append(data[args.text_key])
        filename = os.path.basename(data['location']).split('.')[0]  # Assuming location is the full file path
        filenames.append(filename)
        
    exp_id = str(int(time.time()))
    if not os.path.exists(logdir):
        os.makedirs(logdir)
    output_dir = f"{logdir}/{exp_id}_steps_{num_steps}_guidance_{guidance}_s1_{args.s1}_s2_{args.s2}_b1_{args.b1}_b2_{args.b2}_m_{m}_{schedule_mode}_{model_name}_seed{seed}"
    os.makedirs(output_dir, exist_ok=True)
    
    # Generate and Save #
    model.set_bs(schedule_mode, bs_param,m)
    print(model.sch_mode)
    print(bs_param)
    print(m)
    for k in tqdm(range(0, len(text_prompts), batch_size)):
        batch_text = text_prompts[k: k + batch_size]
        batch_filenames = [f"{output_dir}/{filenames[i]}.wav" for i in range(k, min(k + batch_size, len(filenames)))]
        
        # Check if all files in the batch already exist
        if all(os.path.exists(filename) for filename in batch_filenames):
            print(f"All files in batch {k // batch_size + 1} already exist, skipping...")
            continue
        
        # Generate the batch
        with torch.no_grad():
            latents = model.inference(batch_text, scheduler, num_steps, guidance)
            mel = vae.decode_first_stage(latents)
            wave = vae.decode_to_waveform(mel)
        
        # Save all generated files in the batch
        for i, wav in enumerate(wave):
            sf.write(batch_filenames[i], wav, samplerate=16000)
    
    # Evaluation #
    evaluator = EvaluationHelper(16000, "cuda:0")
    
    result = evaluator.main(output_dir, args.test_references)
    result["Steps"] = num_steps
    result["Guidance Scale"] = guidance
    result["Test Instances"] = len(text_prompts)

    result["scheduler_config"] = dict(scheduler.config)
    result["args"] = dict(vars(args))
    result["output_dir"] = output_dir

    result_filename = f"{logdir}/tango_checkpoint_summary.jsonl"
    with open(result_filename, "a") as f:
        f.write(json.dumps(result) + "\n\n")

if __name__ == "__main__":
    main()