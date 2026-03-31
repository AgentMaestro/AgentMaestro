from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
import os
import json
import tempfile

# Lazy-loaded VOSK model
VOSK_MODEL = None


def get_vosk_model():
    global VOSK_MODEL
    if VOSK_MODEL is None:
        try:
            from vosk import Model
        except Exception as e:
            raise RuntimeError(f"Failed to import vosk: {e}")
        model_path = getattr(settings, 'VOSK_MODEL_PATH', None) or os.environ.get('VOSK_MODEL_PATH')
        if not model_path or not os.path.isdir(model_path):
            raise RuntimeError('VOSK_MODEL_PATH not set or not a directory')
        VOSK_MODEL = Model(model_path)
    return VOSK_MODEL


@csrf_exempt
def stt_transcribe(request, slug=None):
    """
    Accepts raw PCM16LE audio bytes in the request body (with header X-Sample-Rate)
    or a multipart form file named 'audio' (WAV or raw PCM16LE). Returns JSON {text, result}.
    """
    if request.method != 'POST':
        return HttpResponseBadRequest('POST required')

    # Determine sample rate
    try:
        sample_rate = int(request.META.get('HTTP_X_SAMPLE_RATE') or request.POST.get('sample_rate') or 16000)
    except Exception:
        sample_rate = 16000

    raw_bytes = b''
    # Prefer raw body when Content-Type is application/octet-stream
    content_type = request.META.get('CONTENT_TYPE', '')
    if content_type.startswith('application/octet-stream') and request.body:
        raw_bytes = request.body
    elif 'audio' in request.FILES:
        # Read uploaded file. Try to decode using soundfile if it's a WAV/OGG; otherwise read bytes.
        uploaded = request.FILES['audio']
        try:
            import soundfile as sf
            import numpy as np
            # Read file into numpy array (will handle WAV/FLAC/OGG if supported)
            uploaded.seek(0)
            data, sr = sf.read(uploaded, dtype='int16')
            # If stereo, convert to mono
            if len(data.shape) > 1:
                data = data.mean(axis=1).astype('int16')
            if sr != sample_rate:
                # Resample using simple method (requires resampy or similar for higher quality). We'll use soundfile's subtype write/read via tempfile + ffmpeg fallback if necessary.
                try:
                    # Try using resampy if available
                    import resampy
                    data = resampy.resample(data.astype('float32'), sr, sample_rate)
                    # convert back
                    data = (data * 32767).astype('int16')
                except Exception:
                    # As a fallback, write a temp WAV and call ffmpeg if available
                    tf = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
                    tfname = tf.name
                    tf.close()
                    sf.write(tfname, data, sr, subtype='PCM_16')
                    # Use ffmpeg to resample
                    try:
                        import subprocess
                        outname = tfname + '.out.wav'
                        subprocess.check_call(['ffmpeg', '-y', '-i', tfname, '-ar', str(sample_rate), '-ac', '1', '-c:a', 'pcm_s16le', outname], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        # read back
                        data2, sr2 = sf.read(outname, dtype='int16')
                        data = data2
                    finally:
                        try:
                            os.unlink(tfname)
                            if os.path.exists(outname):
                                os.unlink(outname)
                        except Exception:
                            pass
            # raw_bytes expected as int16 little-endian
            if data.dtype != 'int16':
                data = data.astype('int16')
            raw_bytes = data.tobytes()
        except Exception as e:
            # Fall back to raw read
            try:
                uploaded.seek(0)
                raw_bytes = uploaded.read()
            except Exception:
                return JsonResponse({'error': f'Could not read uploaded audio: {e}'}, status=400)
    else:
        # Fallback to request.body if present
        if request.body:
            raw_bytes = request.body
        else:
            return HttpResponseBadRequest('No audio provided')

    # At this point, raw_bytes should contain PCM16LE at sample_rate
    try:
        model = get_vosk_model()
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

    try:
        from vosk import KaldiRecognizer
        rec = KaldiRecognizer(model, sample_rate)
        # If audio is large, feed in chunks
        CHUNK = 4000
        pos = 0
        blen = len(raw_bytes)
        while pos < blen:
            chunk = raw_bytes[pos:pos+CHUNK]
            rec.AcceptWaveform(chunk)
            pos += CHUNK
        res = rec.Result()
        j = json.loads(res)
        text = j.get('text', '')
        return JsonResponse({'text': text, 'result': j})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
