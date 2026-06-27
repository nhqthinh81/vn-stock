"""TAB 6 — PHÁI SINH."""
import streamlit as st


def render(ctx: dict) -> None:
    from vn_invest.phaisinh_tab import render_phaisinh_tab
    render_phaisinh_tab()
