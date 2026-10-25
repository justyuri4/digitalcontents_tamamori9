import inspect

import index.getvoice


def test_recording_service_accepts_speaker_count():
    signature = inspect.signature(index.getvoice.TranscriptionService.start)
    assert "speaker_count" in signature.parameters
