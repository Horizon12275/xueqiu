from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlparse

import requests


class XueqiuError(RuntimeError):
    pass


class XueqiuAuthError(XueqiuError):
    pass


class XueqiuRateLimitError(XueqiuError):
    pass


class XueqiuEndpointError(XueqiuError):
    pass


@dataclass(frozen=True)
class EndpointResponse:
    label: str
    url: str
    params: dict[str, Any]
    data: dict[str, Any] | list[Any]


class XueqiuClient:
    base_url = "https://xueqiu.com"

    def __init__(
        self,
        cookie: str,
        user_agent: str,
        timeout: float = 15.0,
        holding_endpoint_templates: tuple[str, ...] = (),
    ) -> None:
        self.timeout = timeout
        self.holding_endpoint_templates = holding_endpoint_templates
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Connection": "keep-alive",
                "Referer": "https://xueqiu.com/",
                "User-Agent": user_agent,
                "X-Requested-With": "XMLHttpRequest",
            }
        )
        if cookie:
            self.session.headers["Cookie"] = cookie

    def get_rebalancing_history(
        self, cube_symbol: str, page: int = 1, count: int = 20
    ) -> dict[str, Any] | list[Any]:
        return self._get_json(
            "/cubes/rebalancing/history.json",
            {
                "cube_symbol": cube_symbol,
                "count": count,
                "page": page,
            },
            label="rebalancing_history",
            referer=f"https://xueqiu.com/p/{cube_symbol}",
        )

    def get_current_cube_detail(self, cube_symbol: str) -> EndpointResponse:
        path = "/cubes/show.json"
        attempts = (
            ("current_cube_detail_symbol", {"symbol": cube_symbol}),
            ("current_cube_detail_cube_symbol", {"cube_symbol": cube_symbol}),
        )
        last_error: XueqiuEndpointError | None = None
        for label, params in attempts:
            try:
                data = self._get_json(
                    path,
                    params,
                    label=label,
                    referer=f"https://xueqiu.com/p/{cube_symbol}",
                )
            except XueqiuEndpointError as exc:
                last_error = exc
                continue
            return EndpointResponse(
                label=label,
                url=self._absolute_url(path),
                params=params,
                data=data,
            )
        if last_error is not None:
            raise last_error
        raise XueqiuEndpointError("current_cube_detail failed without a response")

    def get_stock_batch_quotes(self, symbols: list[str]) -> dict[str, Any] | list[Any]:
        cleaned_symbols = [symbol.strip().upper() for symbol in symbols if symbol.strip()]
        if not cleaned_symbols:
            return {"data": {"items": []}}
        return self._get_json(
            "https://stock.xueqiu.com/v5/stock/batch/quote.json",
            {"symbol": ",".join(dict.fromkeys(cleaned_symbols))},
            label="stock_batch_quote",
            referer=f"https://xueqiu.com/S/{cleaned_symbols[0]}",
        )

    def get_holding_snapshot_by_rebalance_id(
        self, rb_id: str | int, cube_symbol: str | None = None
    ) -> EndpointResponse:
        responses = self.get_holding_snapshot_candidates(rb_id, cube_symbol)
        if not responses:
            raise XueqiuEndpointError(f"No holding snapshot endpoint succeeded for rb_id={rb_id}")
        return responses[0]

    def get_holding_snapshot_candidates(
        self, rb_id: str | int, cube_symbol: str | None = None
    ) -> list[EndpointResponse]:
        responses: list[EndpointResponse] = []
        for label, path, params in self._holding_endpoint_candidates(rb_id, cube_symbol):
            try:
                data = self._get_json(
                    path,
                    params,
                    label=label,
                    referer=f"https://xueqiu.com/p/{cube_symbol}" if cube_symbol else "https://xueqiu.com/",
                )
            except XueqiuEndpointError:
                continue
            responses.append(
                EndpointResponse(
                    label=label,
                    url=self._absolute_url(path),
                    params=params,
                    data=data,
                )
            )
        return responses

    def _holding_endpoint_candidates(
        self, rb_id: str | int, cube_symbol: str | None
    ) -> list[tuple[str, str, dict[str, Any]]]:
        candidates: list[tuple[str, str, dict[str, Any]]] = []

        for index, template in enumerate(self.holding_endpoint_templates, start=1):
            formatted = template.format(rb_id=rb_id, cube_symbol=cube_symbol or "")
            path, params = self._split_template_url(formatted)
            candidates.append((f"custom_holding_{index}", path, params))

        candidates.extend(
            [
                ("rebalancing_show_rb_id", "/cubes/rebalancing/show.json", {"rb_id": rb_id}),
                (
                    "rebalancing_show_origin_rb_id",
                    "/cubes/rebalancing/show_origin.json",
                    {"rb_id": rb_id},
                ),
                (
                    "rebalancing_show_rebalancing_id",
                    "/cubes/rebalancing/show.json",
                    {"rebalancing_id": rb_id},
                ),
                (
                    "rebalancing_show_origin_rebalancing_id",
                    "/cubes/rebalancing/show_origin.json",
                    {"rebalancing_id": rb_id},
                ),
            ]
        )
        return candidates

    def _get_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        label: str,
        referer: str,
    ) -> dict[str, Any] | list[Any]:
        url = self._absolute_url(path)
        headers = {"Referer": referer}

        try:
            response = self.session.get(
                url,
                params=params,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise XueqiuEndpointError(f"{label} request failed: {exc}") from exc

        if response.status_code == 429:
            raise XueqiuRateLimitError(f"{label} hit HTTP 429 rate limit")
        if response.status_code in {401, 403}:
            raise XueqiuAuthError(f"{label} got HTTP {response.status_code}; Cookie may be invalid")
        if response.status_code >= 500:
            raise XueqiuEndpointError(f"{label} got HTTP {response.status_code}")

        try:
            data = response.json()
        except ValueError as exc:
            raise XueqiuEndpointError(
                f"{label} returned non-JSON response, HTTP {response.status_code}"
            ) from exc

        if isinstance(data, dict):
            error_code = str(data.get("error_code", ""))
            error_description = data.get("error_description") or data.get("message") or ""
            if error_code in {"400016", "401", "403"}:
                raise XueqiuAuthError(
                    f"{label} returned {error_code}: {error_description or 'Cookie may be invalid'}"
                )
            if response.status_code >= 400 or error_code not in {"", "0", "None"}:
                raise XueqiuEndpointError(
                    f"{label} returned {error_code or response.status_code}: {error_description}"
                )

        if response.status_code >= 400:
            raise XueqiuEndpointError(f"{label} got HTTP {response.status_code}")

        return data

    def _absolute_url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.base_url}{path}"

    def _split_template_url(self, url_or_path: str) -> tuple[str, dict[str, Any]]:
        parsed = urlparse(url_or_path)
        if parsed.scheme:
            path = url_or_path.split("?", 1)[0]
        else:
            path = parsed.path or url_or_path.split("?", 1)[0]
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        return path, params
