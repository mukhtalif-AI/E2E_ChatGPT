"""brakes.py - Chatterbox's safety brakes (alignment_stream_analyzer.py, MIT, Resemble AI), re-written with
settings that suit slow narration (used for the trained voice):
  - a pause = the silence sound-piece repeated: Chatterbox ends the line after 2 repeats -> here after
    max_same_token (25 = 1 s)
  - the last word may be held / trail off: Chatterbox cuts after 0.2 s -> here after tail_seconds (0.8 s)
  - the other checks (false start, repeating earlier words after the end) are kept as they were."""
import logging

_log = logging.getLogger("sukoonvoice.brakes")
SET = {"max_same": 25, "tail_frames": 20}


def install(max_same=25, tail_seconds=0.8):
    SET["max_same"] = int(max_same)
    SET["tail_frames"] = max(5, int(round(float(tail_seconds) * 25)))
    import chatterbox.models.t3.t3 as t3mod
    from chatterbox.models.t3.inference.alignment_stream_analyzer import AlignmentStreamAnalyzer as Base
    if getattr(t3mod.AlignmentStreamAnalyzer, "_sukoon", False):
        return

    class Patient(Base):
        _sukoon = True

        def step(self, logits, next_token=None):
            import torch
            aligned_attn = torch.stack(self.last_aligned_attns).mean(dim=0)
            i, j = self.text_tokens_slice
            if self.curr_frame_pos == 0:
                A_chunk = aligned_attn[j:, i:j].clone().cpu()
            else:
                A_chunk = aligned_attn[:, i:j].clone().cpu()
            A_chunk[:, self.curr_frame_pos + 1:] = 0
            self.alignment = torch.cat((self.alignment, A_chunk), dim=0)
            A = self.alignment
            T, S = A.shape
            cur = A_chunk[-1].argmax()
            if -4 < cur - self.text_position < 7:
                self.text_position = cur
            false_start = (not self.started) and (A[-2:, -2:].max() > 0.1 or A[:, :4].max() < 0.5)
            self.started = not false_start
            if self.started and self.started_at is None:
                self.started_at = T
            self.complete = self.complete or self.text_position >= S - 3
            if self.complete and self.completed_at is None:
                self.completed_at = T
            long_tail = self.complete and (A[self.completed_at:, -3:].sum(dim=0).max() >= SET["tail_frames"])
            alignment_repetition = self.complete and (A[self.completed_at:, :-5].max(dim=1).values.sum() > 5)
            tid = None
            if next_token is not None:
                tid = next_token.view(-1)[0].item() if isinstance(next_token, torch.Tensor) else int(next_token)
            run = getattr(self, "_run", [None, 0])
            run = [tid, run[1] + 1] if tid is not None and tid == run[0] else [tid, 1]
            self._run = run
            stuck = tid is not None and run[1] >= SET["max_same"]
            if cur < S - 3 and S > 5:
                logits[..., self.eos_idx] = -2 ** 15                    # not finished the text yet: no ending
            if long_tail or alignment_repetition or stuck:
                _log.info(f"ending line: tail={bool(long_tail)} repeat={bool(alignment_repetition)} stuck={stuck}")
                logits = -(2 ** 15) * torch.ones_like(logits)
                logits[..., self.eos_idx] = 2 ** 15
            self.curr_frame_pos += 1
            return logits

    t3mod.AlignmentStreamAnalyzer = Patient
