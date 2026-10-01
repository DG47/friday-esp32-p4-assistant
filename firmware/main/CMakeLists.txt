idf_component_register(
  SRCS "main.c" "friday_audio.c" "friday_error_scene.c" "friday_backend.c" "friday_state.c" "friday_ui.c" "friday_vad.c" "friday_wifi.c"
  INCLUDE_DIRS "."
  REQUIRES esp_codec_dev esp_event esp_http_server esp_netif esp_timer esp_wifi
           esp_websocket_client json nvs_flash
  EMBED_TXTFILES "friday_backend_ca.pem"
)
