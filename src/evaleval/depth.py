"""Arity-indexed patch chains: One[...], Two[...], Three[...], etc."""

from evaleval.patch import DepthChain

One = DepthChain(1)
Two = DepthChain(2)
Three = DepthChain(3)
Four = DepthChain(4)
Five = DepthChain(5)
Six = DepthChain(6)
Seven = DepthChain(7)
Eight = DepthChain(8)
Nine = DepthChain(9)
Ten = DepthChain(10)

__all__ = [
    "DepthChain",
    "One", "Two", "Three", "Four", "Five",
    "Six", "Seven", "Eight", "Nine", "Ten",
]
