import math


class RatingStrategy:
    """独立可重算的近似策略，不代表原版已公开算法。"""
    def __init__(self, config):
        self.c = config

    def next(self, rating, count, difficulty):
        if count == 0:
            return max(rating+self.c.rating_floor, round(self.c.first_coefficient*difficulty+self.c.first_offset))
        k = self.c.rating_k / (1+count)**self.c.rating_decay
        expected = 1/(1+10**(max(-4000,min(4000,difficulty-rating))/400))
        return rating + max(self.c.rating_floor, round(k*(1-expected)))
