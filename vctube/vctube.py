import json
import os
import shutil
import pandas as pd
import tqdm
import yt_dlp as youtube_dl
import ffmpeg

from collections import OrderedDict
from functools import partial
from glob import glob
from pydub import AudioSegment
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import NoTranscriptFound
from .utils import makedirs, parallel_run
from pytube import extract


class VCtube:
    def __init__(self, output_dir: str, youtube_url: str, lang: str) -> None:
        self.output_dir = output_dir
        self.youtube_url = youtube_url
        self.video_id = youtube_url.split('=')[1]
        self.lang = lang

        # Delete directory if existing
        if os.path.exists(self.output_dir):
            shutil.rmtree(self.output_dir, ignore_errors=True)
        os.makedirs(self.output_dir, exist_ok=True)
        
    def check_vi_available(self):
        try:
            transcript = YouTubeTranscriptApi.get_transcript(self.video_id)
            # if len(transcript) == 0:
            #     return False
            transcript_list = YouTubeTranscriptApi.list_transcripts(self.video_id)
            transcript_list.find_transcript(['vi'])
            return True
        except:
            return False
        
    def download_audio(self) -> None:
        self.download_path = os.path.join(
            self.output_dir, "wavs/" + '%(id)s.%(ext)s')

        # youtube_dl options
        ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'wav',
                'preferredquality': '192'
            }],
            'postprocessors_args': [
                '-ar', '21000'
            ],
            'prefer_ffmpeg': True,
            'keepvideo': False,
            'outtmpl': self.download_path,
            'ignoreerrors': True
        }

        try:
            with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                ydl.download([self.youtube_url])
        except Exception as e:
            print('error', e)

    def download_captions(self, skip_autogenerated=False) -> None:
        lang = self.lang
        video_id = []
        text = []
        start = []
        duration = []
        names = []
        full_names = []
        wav_dir = os.path.join(self.output_dir, "wavs")
        file_list = os.listdir(wav_dir)
        file_list_wav = [file for file in file_list if file.endswith(".wav")]
        for f in tqdm.tqdm(file_list_wav):
            try:
                video = f.split(".wav")[0]

                if skip_autogenerated:
                    try:
                        transcript_list = YouTubeTranscriptApi.list_transcripts(
                            video)
                        subtitle = transcript_list.find_manually_created_transcript([
                                                                                    lang])
                        subtitle = subtitle.fetch()
                    except NoTranscriptFound:
                        msg = "Skipping video {} because it has no manually generated subtitles"
                        print(msg.format(video))
                        continue
                else:
                    subtitle = YouTubeTranscriptApi.get_transcript(
                        video, languages=[lang])

                for s in range(len(subtitle) - 1):
                    video_id.append(video)
                    full_name = os.path.join(
                        wav_dir, video + str(s).zfill(4) + '.wav')
                    full_names.append(full_name)
                    name = video + str(s).zfill(4) + '.wav'
                    names.append(name)
                    subtitle[s]['text'] = ''.join(
                        [c for c in subtitle[s]['text'] if c not in ('!', '?', ',', '.', '\n', '~', '"', "'")])
                    text.append(subtitle[s]['text'])
                    start.append(subtitle[s]['start'])

                    #####################
                    if subtitle[s]['duration'] >= (subtitle[s + 1]['start'] - subtitle[s]['start']):
                        duration.append(
                            subtitle[s + 1]['start'] - subtitle[s]['start'])
                    else:
                        duration.append(subtitle[s]['duration'])
                    #####################

            except Exception as e:
                print("error:", e)

        df = pd.DataFrame({"id": video_id, "text": text,
                          "start": start, "duration": duration, "name": full_names})
        text_dir = os.path.join(self.output_dir, "text")
        makedirs(text_dir)

        df.to_csv(text_dir + '/subtitle.csv', encoding='utf-8')
        res = [i + '|' + j for i, j in zip(names, text)]
        df2 = pd.DataFrame({"name": res})
        df2.to_csv(os.path.join(self.output_dir, 'metadata.csv'),
                   encoding='utf-8', header=False, index=False)
        file_data = OrderedDict()
        for i in range(df.shape[0]):
            file_data[df['name'][i]] = df['text'][i]
        with open(os.path.join(self.output_dir, 'alignment.json'), 'w', encoding="utf-8") as make_file:
            json.dump(file_data, make_file, ensure_ascii=False, indent="\n")

        print(os.path.basename(self.output_dir) + ' channel was finished')

    def audio_split(self, parallel=False) -> None:
        base_dir = self.output_dir + '/wavs/*.wav'
        audio_paths = glob(base_dir)
        audio_paths.sort()
        fn = partial(split_with_caption)
        parallel_run(fn, audio_paths, desc="Split with caption",
                     parallel=parallel)

    def remove_audio(self):
        id = extract.video_id(self.youtube_url)
        # os.remove(self.output_dir + "/wavs/" + id + "webm.wav")
        os.remove(self.output_dir + "/wavs/" + id + ".wav")
        
        

    def operations(self):
        self.download_audio()
        self.download_captions()
        self.audio_split()
        self.remove_audio()


def split_with_caption(audio_path, skip_idx=0, out_ext="wav") -> list:

    df = pd.read_csv(audio_path.split('wavs')[0] + 'text/subtitle.csv')
    filename = os.path.basename(audio_path).split('.', 1)[0]

    audio = read_audio(audio_path)
    df2 = df[df['id'].apply(str) == filename]

    ####################################################
    df2['end'] = round((df2['start'] + df2['duration']) * 1000).astype(int)
    df2['start'] = round(df2['start'] * 1000).astype(int)
    ####################################################

    edges = df2[['start', 'end']].values.tolist()

    audio_paths = []
    for idx, (start_idx, end_idx) in enumerate(edges[skip_idx:]):
        start_idx = max(0, start_idx)

        target_audio_path = "{}/{}{:04d}.{}".format(
            os.path.dirname(audio_path), filename, idx, out_ext)

        segment = audio[start_idx:end_idx]

        segment.export(target_audio_path, "wav")  # for soundsegment

        audio_paths.append(target_audio_path)

    return audio_paths


def read_audio(audio_path):
    return AudioSegment.from_file(audio_path)