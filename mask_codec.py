# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

"""Conversions between Blender conventions and the GoZ wire format.

The two sides disagree about what a mask value means, which is easy to get
wrong and impossible to spot by reading either file in isolation:

    Blender ``.sculpt_mask``   FLOAT per vertex, 0.0 = unmasked, 1.0 = masked
    Blender "mask" group       weight, 0.0 = unmasked, 1.0 = masked
    GoZ mask section (``3275``) u16 per vertex, 65535 = unmasked, 0 = masked

The GoZ format stores *unmaskedness*, Blender stores *maskedness*, so every
crossing of the bridge has to invert. Keeping both directions in one place is
what stops an import from inverting what the export just wrote (which flipped
the mask on every Blender -> ZBrush -> Blender round trip).

Face sets share the same trap: value 65504 is reserved by ZBrush to mean
"no polygroup", so it must never be written back as a real masked/grouped
value.
"""

import numpy as np

# ZBrush reserves this face-set value to mean "no polygroup".
NO_POLYGROUP = 65504

# A GoZ mask record is a u16, so this is a full "not masked".
UNMASKED_U16 = 65535


def bl_to_goz_mask(values):
    """Convert Blender maskedness in 0..1 to GoZ u16 unmaskedness."""
    values = np.asarray(values, dtype=np.float32)
    np.clip(values, 0.0, 1.0, out=values)
    return np.rint((1.0 - values) * float(UNMASKED_U16)).astype("<u2")


def goz_to_bl_mask(records):
    """Convert GoZ u16 unmaskedness to Blender maskedness in 0..1.

    The exact inverse of :func:`bl_to_goz_mask`.
    """
    records = np.asarray(records, dtype=np.uint16)
    return (float(UNMASKED_U16) - records.astype(np.float32)) / float(UNMASKED_U16)


def goz_to_bl_weight(records):
    """Same as :func:`goz_to_bl_mask`, named for the vertex-group path.

    A GoZ mask record is unmaskedness, so feeding it straight into a vertex
    group weight stored the complement of the mask and every round trip
    flipped it.
    """
    return goz_to_bl_mask(records)
