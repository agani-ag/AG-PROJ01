/* Shared Leaflet helpers for CRM lead maps (used by lead_edit + lead_view). */
(function (global) {
  "use strict";

  // Add the standard base layers + layer switcher to a map. Returns the base maps.
  function crmBaseLayers(map, defaultKey) {
    const layers = {
      "☀️ Light": L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
        { attribution: '&copy; OpenStreetMap &copy; CartoDB' }),
      "🌙 Dark": L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
        { attribution: '&copy; OpenStreetMap &copy; CartoDB' }),
      "🗺️ Google": L.tileLayer('https://{s}.google.com/vt/lyrs=m&x={x}&y={y}&z={z}',
        { attribution: '&copy; Google Maps', subdomains: ['mt0', 'mt1', 'mt2', 'mt3'] }),
      "⛰️ Terrain": L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
        { attribution: '&copy; OpenTopoMap' }),
      "🗺️ OSM": L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
        { attribution: '&copy; OpenStreetMap' }),
      "🛰️ Satellite": L.tileLayer('https://{s}.google.com/vt/lyrs=y&x={x}&y={y}&z={z}',
        { attribution: '&copy; Google Maps', subdomains: ['mt0', 'mt1', 'mt2', 'mt3'] }),
    };
    (layers[defaultKey] || layers["🗺️ Google"]).addTo(map);
    L.control.layers(layers).addTo(map);
    return layers;
  }

  // Add a small square "📍" button to the map.
  function crmLocateControl(map, title, onClick) {
    const ctrl = L.control({ position: 'topleft' });
    ctrl.onAdd = function () {
      const div = L.DomUtil.create('div', 'leaflet-bar leaflet-control leaflet-control-custom');
      Object.assign(div.style, {
        backgroundColor: 'white', width: '34px', height: '34px', cursor: 'pointer',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      });
      div.title = title;
      div.innerHTML = '📍';
      div.onclick = function (e) { e.preventDefault(); e.stopPropagation(); onClick(); };
      return div;
    };
    ctrl.addTo(map);
    return ctrl;
  }

  global.crmBaseLayers = crmBaseLayers;
  global.crmLocateControl = crmLocateControl;
})(window);
