"""Shared mix levels for building karaoke instrumentals."""
import math

# Linear amplitude gain applied to the backing-vocals stem when it is mixed back
# into the clean instrumental (the "Instrumental +BV" track and custom
# instrumentals). At 1.0 the backing vocals sounded too loud in the final
# karaoke mix; 0.7 is ~30% quieter (about -3.1 dB).
BACKING_VOCALS_MIX_GAIN = 0.7

BACKING_VOCALS_MIX_GAIN_DB = 20 * math.log10(BACKING_VOCALS_MIX_GAIN)
