"""
Common style of the article figures.

White background, vector PDF with embedded TrueType fonts, sizes in inches of
the printed figure (the article includes them at the text width, so the font
sizes below are the printed sizes), and one colour-blind-safe palette
(Okabe-Ito) used with distinct markers, so that series also differ in grey.

Project: PINV01-24 - FIUNA
"""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# Printed widths (inches): the text width of the article and a narrower one
FULL_WIDTH = 6.3
NARROW_WIDTH = 4.8

BLUE = "#0072B2"
ORANGE = "#E69F00"
VERMILION = "#D55E00"
GREEN = "#009E73"
SKY = "#56B4E9"
PURPLE = "#CC79A7"
INK = "#1a1a1a"
GREY = "#6b6b6b"
GRID = "#e3e3e3"

RC = {
    "font.family": "DejaVu Sans",
    "font.size": 8.5,
    "axes.titlesize": 9,
    "axes.labelsize": 8.5,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "legend.fontsize": 7.5,
    "axes.edgecolor": GREY,
    "axes.labelcolor": INK,
    "text.color": INK,
    "xtick.color": GREY,
    "ytick.color": GREY,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.color": GRID,
    "grid.linewidth": 0.6,
    "axes.axisbelow": True,
    "legend.frameon": False,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "savefig.facecolor": "white",
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
}


def apply():
    """Set the common style for every figure drawn after this call."""
    plt.rcParams.update(RC)


def save(fig, out_dir, name):
    """Write <name>.pdf (for the article) and <name>.png (to look at); returns the PDF path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf = out_dir / f"{name}.pdf"
    fig.savefig(pdf)
    fig.savefig(out_dir / f"{name}.png")
    plt.close(fig)
    return pdf


def panel_title(ax, text):
    ax.set_title(text, loc="left")
