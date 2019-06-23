# Copyright 2017, Michael Dorman.
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.

from beets import mediafile
from beets import plugins
from beets import ui
from beets.dbcore import types
from beets.dbcore.types import Integer
from beets.library import Item
from mutagen.id3._frames import POPM


class NullInteger(Integer):
    """Same as `Integer`, but does not normalize `None` to `0` but '-1'.
    """
    null = None


NULL_INTEGER = NullInteger()


class UserRatingsPlugin(plugins.BeetsPlugin):
    """
    A plugin for managing track ratings.

    We're using the POPM tag http://id3.org/id3v2.3.0#Popularimeter as
    our storage format (values 0-255, with 0 meaning no rating).
    """

    item_types = {
        'userrating': NULL_INTEGER
    }

    def __init__(self):
        super(UserRatingsPlugin, self).__init__()

        self.config.add({
            # Should we automatically import any values we find?
            'auto': True,
            # Should we overwrite an existing entry?
            'overwrite': False,
        })

        # Add importing ratings to the import process
        if self.config['auto']:
            self.import_stages = [self.imported]

        # Given the complexity of the storage style implementations, I
        # find it handy to allow them to do unified logging.
        userrating_field = mediafile.MediaField(
            MP3UserRatingStorageStyle(_log=self._log),
            UserRatingStorageStyle(_log=self._log),
            ASFRatingStorageStyle(_log=self._log),
            out_type=int
        )

        if 'userrating' not in mediafile.MediaFile.__dict__:
            self.add_media_field('userrating', userrating_field)

    # We do present a command, though it doesn't do anything as yet
    def commands(self):
        """
        Return the "userrating" ui subcommand.
        """

        cmd = ui.Subcommand('userrating', help=u'manage user ratings for tracks')
        cmd.func = lambda lib, opts, args: self.handle_tracks(lib.items(ui.decargs(args)), opts)
        cmd.parser.add_option(
            u'-u', u'--update', action='store',
            help=u'all files will be rated with given value',
        )
        cmd.parser.add_option(
            u'-o', u'--overwrite', action='store_true',
            help=u'allow overwriting rated file (default is to skip already rated file)',
        )
        return [cmd]

    def imported(self, session, task):
        """
        Add rating info to items of ``task`` during import.
        """

        self.handle_tracks(task.imported_items())

    def handle_tracks(self, items, opts):
        """
        Abstract out our iteration code.
        """

        for item in items:
            self.handle_track(item, opts)

    def handle_track(self, item, opts):
        """
        Ask for user rating for track and store it in the item.

        If user rating information is already present in the item,
        nothing is done unless ``overwrite`` has been set.
        """
        if opts.update is None:
            self.display_track_rating(item)
        else:
            self.update_track_rating(item, opts)

    def display_track_rating(self, item):
        if 'userrating' in item:
            self._log.info(u'{0} is rated with {1}', item, item.userrating)
        else:
            self._log.warning(u'{0} is not rated', item)

    def update_track_rating(self, item, opts):
        should_write = ui.should_write()
        self._log.debug(u'Getting rating for {0}', item)
        # Get any rating already in the file
        rating = item.userrating if 'userrating' in item else None
        self._log.debug(u'Found rating value "{0}"', rating)
        if not rating or opts.overwrite:
            item['userrating'] = int(opts.update)
            if should_write and item.try_write():
                item.store()
                self._log.info(u'Applied rating {0}', opts.update)
        else:
            # We should consider asking here
            self._log.info(u'skip already-rated track {0}', item.path)


class MP3UserRatingStorageStyle(mediafile.MP3StorageStyle):
    """
    A codec for MP3 user ratings in files.

    Since we chose to use POPM as our baseline,, we don't have to do
    any conversion, just look for the various possible tags

    """
    TAG = 'POPM'

    def __init__(self, **kwargs):
        self._log = kwargs.get('_log')
        super(MP3UserRatingStorageStyle, self).__init__(self.TAG)

    # The ordered list of which "email" entries we will look
    # for/prioritize in POPM tags.  Should eventually be configurable.
    popm_order = ["no@email", "Windows Media Player 9 Series", "rating@winamp.com", "", "Banshee"]

    def get(self, mutagen_file):
        # Create a map of all our email -> rating entries
        user_ratings = {frame.email: frame.rating for frame in mutagen_file.tags.getall(self.TAG)}
        # Find the first entry from popm_order, or None
        return next((user_ratings.get(user) for user in self.popm_order if user in user_ratings), None)

    def get_list(self, mutagen_file):
        raise NotImplementedError(u'MP3 Rating storage does not support lists')

    def set(self, mutagen_file, value):
        if value is not None:
            for user in self.popm_order:
                if mutagen_file.tags.getall(self.TAG) is None:
                    mutagen_file[self.TAG] = POPM(value, user)

    def set_list(self, mutagen_file, values):
        raise NotImplementedError(u'MP3 Rating storage does not support lists')


class UserRatingStorageStyle(mediafile.StorageStyle):
    """
    A codec for user ratings in files using an accepted format (still not normalized)
    format which is RATING:[:@email]=value
    Note that for FLAC/ALAC, value seems to be between 0 and 100
    For other format, the 0 to 255 value still seems to be the accepted range.
    """

    TAG = 'RATING'

    def __init__(self, **kwargs):
        self._log = kwargs.get('_log')
        # We don't have a set tag
        super(UserRatingStorageStyle, self).__init__(self.TAG)

    # The ordered list of which "email" entries we will look
    # for/prioritize in POPM tags.  Should eventually be configurable.
    popm_order = ["no@email", "Windows Media Player 9 Series", "rating@winamp.com", "", "Banshee"]

    def get(self, mutagen_file):
        tag = self.TAG
        return next((int(float(mutagen_file.get(tag)[0]) * 255) for tag in self.popm_order if
                     mutagen_file.tags.get(self.TAG) is not None),
                    None)

    def get_list(self, mutagen_file):
        raise NotImplementedError(u'UserRating storage does not support lists')

    def set(self, mutagen_file, value):
        if value is not None:
            max_value = 100 if 'audio/flac' in mutagen_file.mime else 255
            val = value / 255 * max_value
            for user in self.popm_order:
                mutagen_file["RATING:{0}".format(user)] = str(val)

    def set_list(self, mutagen_file, values):
        raise NotImplementedError(u'UserRating storage does not support lists')


class ASFRatingStorageStyle(mediafile.ASFStorageStyle):
    """
    A codec for user ratings in ASF/WMA unsing Windows MEdia player tag format
    """

    TAG = 'WM/SharedUserRating'

    asf_order = ["no@email"]

    def __init__(self, **kwargs):
        self._log = kwargs.get('_log')
        # We don't have a set tag
        super(ASFRatingStorageStyle, self).__init__(self.TAG)

    def get(self, mutagen_file):
        # Create a map of all our email -> rating entries
        if mutagen_file.tags.get(self.TAG) is not None:
            user_ratings = {frame.email: int(frame.rating) for frame in mutagen_file.tags.get(self.TAG)}
        else:
            user_ratings = {self.asf_order[0]: None}

        # Find the first entry from asf_order, or None
        return next((user_ratings.get(user) for user in self.asf_order if user in user_ratings), None)

    def get_list(self, mutagen_file):
        raise NotImplementedError(u'MP3 Rating storage does not support lists')

    def set(self, mutagen_file, value):
        if value is not None:
            for user in self.asf_order:
                tag = "{0}:{1}".format(self.TAG, user)
                mutagen_file[tag] = value

    def set_list(self, mutagen_file, values):
        raise NotImplementedError(u'MP3 Rating storage does not support lists')
