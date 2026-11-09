import math
import unittest

from tools.wechat_channels.engagement import (
    MAX_COUNT, engagement_from_feed, merge_engagement, normalize_engagement, valid_count,
)


class EngagementTests(unittest.TestCase):
    def test_verified_icon_order_and_exact_counts(self):
        self.assertEqual(engagement_from_feed({'favCount': 34002, 'forwardCount': 70268,
            'likeCount': 23402, 'commentCount': 3712}),
            {'recommend': 34002, 'share': 70268, 'like': 23402, 'comment': 3712})

    def test_zero_and_integral_numbers_are_valid(self):
        for value in (0, 1, 2.0, MAX_COUNT):
            self.assertEqual(valid_count(value), int(value))

    def test_unavailable_counts_are_never_fabricated_as_zero(self):
        for value in (True, False, '1', '', -1, 1.2, math.inf, -math.inf, math.nan,
                      MAX_COUNT + 1, {}, [], None):
            self.assertIsNone(valid_count(value))
        self.assertEqual(normalize_engagement(None),
                         {'recommend': None, 'share': None, 'like': None, 'comment': None})

    def test_known_new_values_can_decrease_to_zero(self):
        result = merge_engagement({'recommend': 10, 'share': 20, 'like': 30, 'comment': 40},
                                  {'recommend': 0, 'share': None, 'like': True, 'comment': 39})
        self.assertEqual(result, {'recommend': 0, 'share': 20, 'like': 30, 'comment': 39})
