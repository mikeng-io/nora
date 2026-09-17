#!/usr/bin/env python3
"""Inspect trained ONNX input/output shapes + summary from train log."""
import os
import sys

import onnx

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
m = onnx.load(os.path.join(LAB, "oww-data", "output", "喂Nora.onnx"))
print("inputs:")
for i in m.graph.input:
    dims = [d.dim_value for d in i.type.tensor_type.shape.dim]
    print(" ", i.name, dims)
print("outputs:")
for o in m.graph.output:
    dims = [d.dim_value for d in o.type.tensor_type.shape.dim]
    print(" ", o.name, dims)
