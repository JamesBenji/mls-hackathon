# Medicall branding

The app mark is a white cross on a teal rounded tile. `medicall.svg` is the vector source; `medicall.png` is the 1024-pixel raster reference. These assets replace Flutter's generated launcher icons without changing the apps' identities.

To regenerate Android, iOS, web and Windows icons from the main repository, install Pillow in your Python environment and run:

```sh
python tools/generate_icons.py
```

The Windows icon is generated when `dashboard/windows/runner/resources/` exists. During development in a separate worktree, pass `--dashboard <path-to-dashboard>` to update that worktree instead. Rebuild each app to apply native launcher changes; operating-system icon caches may delay their appearance.
