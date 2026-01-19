import random
import string


class WordUtils:
    """Utility class for word-related operations."""

    _used_ids = set()  # Keep track of used IDs
    _id_length = 6  # Length of generated IDs

    @classmethod
    def generate_id(cls) -> str:
        """Generate a unique ID for words/segments.

        Uses a combination of letters and numbers to create an 8-character ID.
        With 36 possible characters (26 letters + 10 digits), this gives us
        36^8 = ~2.8 trillion possible combinations, which is more than enough
        for our use case while being much shorter than UUID.
        """
        while True:
            # Generate random string of letters and numbers
            new_id = "".join(random.choices(string.ascii_lowercase + string.digits, k=cls._id_length))

            # Make sure it's unique for this session
            if new_id not in cls._used_ids:
                cls._used_ids.add(new_id)
                return new_id
