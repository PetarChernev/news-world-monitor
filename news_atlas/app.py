from datetime import datetime
from typing import List, Optional, Tuple
from dash import Dash, ctx, dcc, html, Input, Output, State, no_update
import plotly
import plotly.express as px
import pandas as pd
import numpy as np

from news_data_loader import NewsDataLoader
from article_smart_cache import article_cache

# -----------------------------
# Dash app
# -----------------------------
loader = NewsDataLoader(database="world-news-knowledge")

# We'll use gapminder countries to get a consistent world ISO-3 map and readable names.
gm = px.data.gapminder()
gm2007 = gm[gm["year"] == 2007].drop_duplicates("iso_alpha")
iso_to_country = dict(zip(gm2007["iso_alpha"], gm2007["country"]))
all_iso = gm2007["iso_alpha"].tolist()

def make_map_figure(series_by_iso3: pd.Series, selected_iso3: Optional[str], colorbar_title: str) -> "plotly.graph_objs._figure.Figure":
    """Build choropleth for a given ISO3->value series."""
    # align to known ISO-3 codes so hover/click behaves consistently
    df = pd.DataFrame({"iso_alpha": all_iso})
    series = pd.Series(series_by_iso3, dtype="float64")
    df["value"] = df["iso_alpha"].map(series)

    # Determine range (optional: auto if empty or all-zero)
    if series.size > 0 and series.max(skipna=True) > 0:
        zmin = float(np.nanmin(df["value"]))
        zmax = float(np.nanmax(df["value"]))
        # guard if all values nan
        if not np.isfinite(zmin) or not np.isfinite(zmax):
            zmin, zmax = None, None
    else:
        zmin = zmax = None

    fig = px.choropleth(
        df,
        locations="iso_alpha",
        color="value",
        hover_name=df["iso_alpha"].map(iso_to_country),
        color_continuous_scale="Viridis",
        range_color=(zmin, zmax) if (zmin is not None and zmax is not None) else None,
        scope="world",
        projection="natural earth",
    )

    fig.update_traces(
        marker_line_width=0.5,
        marker_line_color="#aaaaaa",
        hovertemplate="%{hovertext}<br>Value: %{z}<extra></extra>",
    )
    fig.update_coloraxes(colorbar_title=colorbar_title, colorbar_len=0.75)
    fig.update_geos(showframe=False, showcoastlines=True, coastlinecolor="#888", bgcolor="rgba(0,0,0,0)")
    fig.update_layout(margin=dict(l=0, r=0, t=0, b=0), clickmode="event+select", uirevision="keep")

    # Outline selected country (if any)
    if selected_iso3:
        sel_df = df[df["iso_alpha"] == selected_iso3].copy()
        sel_df["value"] = np.nan
        fig.add_choropleth(
            locations=sel_df["iso_alpha"],
            z=sel_df["value"],
            locationmode="ISO-3",
            marker_line_color="#222",
            marker_line_width=2.5,
            colorscale=[[0, "rgba(0,0,0,0)"], [1, "rgba(0,0,0,0)"]],
            showscale=False,
            hoverinfo="skip",
        )

    return fig


app = Dash(__name__)
server = app.server
app.title = "News Topics Map — Firestore + Dash"

app.layout = html.Div(
    style={"maxWidth": "1600px", "margin": "0 auto", "fontFamily": "system-ui, sans-serif", "padding": "1rem"},
    children=[
        html.H2("World News — Hot Topics by Country"),
        html.Div(
            style={"marginBottom": "0.75rem"},
            children=[
                html.Div(
                    id="hour-display",
                    style={"fontWeight": 600, "marginBottom": "0.35rem"}
                ),
                dcc.Slider(
                    id="hour-slider",
                    min=0, max=0, step=1, value=0,  # will be set by init callback
                    tooltip={"always_visible": False, "placement": "bottom"},
                    updatemode="drag",
                ),
            ],
        ),

        # --- Topic panel ---
        # --- Two-column content row: left (topics + map), right (articles) ---
        html.Div(
            id="content-row",
            style={
                "display": "flex",
                "gap": "0.75rem",
                "alignItems": "flex-start",
            },
            children=[
                # LEFT: topics + world map (≈ 3/4 width)
                html.Div(
                    id="left-col",
                    style={"flex": "1 1 75%", "minWidth": 0},
                    children=[
                        # --- Topic panel ---
                        html.Div(
                            id="topics-panel",
                            style={
                                "border": "1px solid #eaeaea",
                                "borderRadius": "10px",
                                "padding": "0.75rem",
                                "marginBottom": "0.75rem",
                                "background": "#fafafa",
                            },
                            children=[
                                html.Div(id="topics-header", style={"fontWeight": 600, "marginBottom": "0.5rem"}),
                                dcc.RadioItems(
                                    id="topic-chooser",
                                    options=[],
                                    value=None,
                                    inline=True,
                                    labelStyle={"display": "inline-block", "marginRight": "1rem", "cursor": "pointer"},
                                    inputStyle={"marginRight": "0.25rem"},
                                ),
                                html.Div(
                                    id="active-topic-readout",
                                    style={"marginTop": "0.5rem", "color": "#333"}
                                ),
                                html.Button(
                                    "Clear active topic",
                                    id="clear-topic",
                                    n_clicks=0,
                                    style={"marginTop": "0.5rem"},
                                    disabled=False,
                                ),
                            ],
                        ),

                        # --- Map ---
                        dcc.Graph(
                            id="world-map",
                            style={"height": "72vh", "border": "1px solid #ddd", "borderRadius": "10px"}
                        ),
                    ],
                ),

                # RIGHT: articles sidebar (≈ 1/4 width)
                html.Div(
                    id="right-col",
                    style={
                        "flex": "0 0 25%",
                        "minWidth": "260px",
                    },
                    children=[
                        html.Div(
                            id="articles-panel",
                            style={
                                "border": "1px solid #eaeaea",
                                "borderRadius": "10px",
                                "padding": "0.75rem",
                                "background": "#fff",
                                "position": "sticky",     # keep in view while scrolling
                                "top": "1rem",
                                "maxHeight": "82vh",
                                "overflowY": "auto",
                            },
                            children=[
                                html.Div("Articles", style={"fontWeight": 600, "marginBottom": "0.5rem"}),
                                html.Div(id="articles-list"),
                            ],
                        ),
                    ],
                ),
            ],
        ),



        # --- Stores (app state) ---
        dcc.Store(id="hours-list", data=[]),   # list of available hour strings
        dcc.Interval(id="init", interval=250, n_intervals=0, max_intervals=1),
        dcc.Store(id="hour-store", data=None),
        dcc.Store(id="selected-iso3", data=None),           # single-country selection (or None)
        dcc.Store(id="active-topic", data=None),            # currently active topic (or None)
        dcc.Store(id="country-totals-store", data={}),      # { ISO3: count } for hour
        dcc.Store(id="global-top-entities-store", data=[]), # [ (topic, count), ... ] for hour
        dcc.Store(id="entity-breakdown-store", data={}),    # { ISO3: count } for active topic
    ],
)


# -----------------------------
# Data loading callbacks
# -----------------------------
@app.callback(
    Output("country-totals-store", "data"),
    Output("global-top-entities-store", "data"),
    Input("hour-store", "data"),
)
def load_hour_data(hour):
    """Fetch country totals and global top topics for the hour."""
    if not hour:  # <-- guard
        return {}, []
    # country totals
    totals = loader.country_totals(hour)
    totals_dict = totals.astype(int).to_dict()

    # global top entities (pairs so we can show counts in labels if desired)
    tops = loader.top_entities(hour, limit=20)
    tops_list: List[Tuple[str, int]] = [(str(name), int(count)) for name, count in tops.items()]

    return totals_dict, tops_list


# When active topic changes, fetch country breakdown for that entity
@app.callback(
    Output("entity-breakdown-store", "data"),
    Input("active-topic", "data"),
    State("hour-store", "data"),
    prevent_initial_call=True,
)
def load_entity_breakdown(active_topic, hour):
    if not active_topic :
        return {}
    s = loader.country_breakdown_for_entity(hour, active_topic)
    return {k: int(v) for k, v in s.items()}

# -----------------------------
# Hour selector logic
# -----------------------------

@app.callback(
    Output("hour-slider", "min"),
    Output("hour-slider", "max"),
    Output("hour-slider", "marks"),
    Output("hour-slider", "value"),
    Output("hours-list", "data"),
    Input("init", "n_intervals"),
    prevent_initial_call=False,
)
def init_hours(_):
    hours = loader.list_hours()

    # Fallback to the hardcoded hour if Firestore is unreachable/empty.
    if not hours:
        raise Exception(f"Could not load hours from Firestore.")

    hours_sorted = sorted(hours)  # ascending
    marks = {i: h for i, h in enumerate(hours_sorted)}

    value_idx = len(hours_sorted) - 1

    return (
        0,
        len(hours_sorted) - 1,
        marks,
        value_idx,
        hours_sorted,
    )

@app.callback(
    Output("hour-store", "data"),
    Input("hour-slider", "value"),
    State("hours-list", "data"),
)
def set_hour_from_slider(idx, hours):
    if hours:
        if isinstance(idx, int) and 0 <= idx < len(hours):
            return hours[idx]
        return hours[-1]
    return no_update


@app.callback(
    Output("hour-display", "children"),
    Input("hour-store", "data"),
)
def show_hour(h):
    return f"Hour: {h}" if h else "Hour: (none)"

# -----------------------------
# Topic panel logic
# -----------------------------
@app.callback(
    Output("topics-header", "children"),
    Output("topic-chooser", "options"),
    Output("topic-chooser", "value"),
    Input("selected-iso3", "data"),
    Input("global-top-entities-store", "data"),
    State("hour-store", "data"),
)
def build_topics_panel(selected_iso3, global_tops, hour):
    """
    - No country selected: show global top topics (no topic pre-selected).
    - Country selected: fetch that country's top topics.
    """
    if selected_iso3:
        # Country-specific top 1
        try:
            by_country = loader.top_entities_by_country(hour, selected_iso3, limit=20)
            if not by_country.empty:
                header = f"Hot topics in {iso_to_country.get(selected_iso3, selected_iso3)}"
                options = [{"label": f"{name} ({count})", "value": name} for name, count in (by_country.items() or [])]
                return header, options, None
            else:
                header = f"No topics found for {iso_to_country.get(selected_iso3, selected_iso3)}"
                return header, [], None
        except Exception:
            header = f"Topics unavailable for {iso_to_country.get(selected_iso3, selected_iso3)}"
            return header, [], None
    else:
        # Global panel
        header = "Global hot topics"
        options = [{"label": f"{name} ({count})", "value": name} for name, count in (global_tops or [])]
        return header, options, None


# Clicking a topic activates it (colors the map by that entity)
@app.callback(
    Output("active-topic", "data"),
    Input("topic-chooser", "value"),
    Input("clear-topic", "n_clicks"),   # <-- NEW
    State("active-topic", "data"),
    prevent_initial_call=True,
)
def set_active_topic_from_click(chosen, clear_clicks, active_topic):
    # If the clear button was clicked, clear the active topic.
    if ctx.triggered_id == "clear-topic":
        return None
    # Otherwise, set from topic chooser (if any)
    if chosen:
        return chosen
    return no_update


# Status line under the topic chips
@app.callback(
    Output("active-topic-readout", "children"),
    Input("active-topic", "data"),
)
def show_active_topic(active_topic):
    return f"Active topic: {active_topic}" if active_topic else "Active topic: (none)"

@app.callback(
    Output("clear-topic", "disabled"),
    Input("active-topic", "data"),
)
def _toggle_clear_button(active_topic):
    return active_topic is None

# -----------------------------
# Map interactions & rendering
# -----------------------------
@app.callback(
    Output("selected-iso3", "data"),
    Input("world-map", "clickData"),
    State("selected-iso3", "data"),
    prevent_initial_call=True,
)
def toggle_country(click_data, selected_iso3):
    if click_data and "points" in click_data and click_data["points"]:
        loc = click_data["points"][0].get("location")
        if isinstance(loc, str):
            return None if selected_iso3 == loc else loc
        return None
    return no_update


@app.callback(
    Output("world-map", "figure"),
    Input("country-totals-store", "data"),
    Input("entity-breakdown-store", "data"),
    Input("active-topic", "data"),
    Input("selected-iso3", "data"),
)
def render_map(totals_dict, entity_dict, active_topic, selected_iso3):
    if active_topic and entity_dict:
        series = pd.Series(entity_dict, dtype="float64")
        colorbar_title = f"{active_topic} — mentions"
    else:
        series = pd.Series(totals_dict or {}, dtype="float64")
        colorbar_title = "All topics — mentions"

    return make_map_figure(series, selected_iso3, colorbar_title)


@app.callback(
    Output("articles-list", "children"),
    Input("hour-store", "data"),
    Input("selected-iso3", "data"),
    Input("active-topic", "data"),
    State("country-totals-store", "data"),
    State("entity-breakdown-store", "data"),
    State("global-top-entities-store", "data"),
)
def update_articles_list(hour, iso3, topic, totals_dict, entity_dict, global_tops):
    if not hour:
        return html.Div("No hour selected.", style={"color": "#666"})

    try:
        rows = article_cache.get(
            hour=hour,
            country=iso3,
            entity=topic,
            totals_by_iso=totals_dict if isinstance(totals_dict, dict) else None,
            entity_breakdown_by_iso=entity_dict if isinstance(entity_dict, dict) else None,
            global_top_entities=global_tops if isinstance(global_tops, list) else None,
            loader=loader,
            per_page=3,
        )
    except Exception as e:
        return html.Div(f"Failed to load articles: {e}", style={"color": "crimson"})

    if not rows:
        scope_bits = []
        if topic: scope_bits.append(f"topic “{topic}”")
        if iso3: scope_bits.append(f"country {iso_to_country.get(iso3, iso3)}")
        scope_str = f" for {' and '.join(scope_bits)}" if scope_bits else ""
        return html.Div(f"No articles found{scope_str}.", style={"color": "#666"})

    items = []
    for art in rows:
        title = art.get("title") or "(untitled)"
        url = art.get("url") or "#"
        t = art.get("time")
        try:
            dt = pd.to_datetime(t) if not isinstance(t, datetime) else t
            ts = dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            ts = str(t) if t is not None else ""
        items.append(
            html.Div(
                style={"padding": "0.5rem 0", "borderTop": "1px solid #f0f0f0"},
                children=[
                    html.A(
                        title, href=url, target="_blank",
                        style={
                            "display": "block", "fontWeight": 600,
                            "textDecoration": "none", "color": "#0b6cff",
                            "marginBottom": "0.25rem", "wordBreak": "break-word",
                        },
                    ),
                    html.Span(ts, style={"fontSize": "0.9rem", "color": "#666"}),
                ],
            )
        )

    return items


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)