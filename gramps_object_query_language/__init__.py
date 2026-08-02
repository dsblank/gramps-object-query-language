#
# gramps-object-query-language - Object query language and SQL compiler for Gramps data
#
# Copyright (C) 2026      Douglas Blank
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#

"""Object query language and SQL compiler for Gramps data.

Standalone, privacy-agnostic: compiles a structured query (select/where/
order_by/limit/after) or an "almost Python" expression string into
parameterized SQL against Gramps' flattened secondary columns. Carries no
knowledge of proxies, permissions, or Gramps Web API request handling --
callers are responsible for only invoking it against an unproxied database
handle.
"""
