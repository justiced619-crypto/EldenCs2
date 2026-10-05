# Elden Ring firearm prototype

## Scope

An offline, data-only prototype using Elden Ring's existing Jar Cannon, Ballista Bolt, projectile, and firing/reload behavior. All 12 selectable starting origins equip the gun and matching ammo at character creation. It does not use Counter-Strike 2 files or modify the CS2 client. ModEngine 2 is the intended Elden Ring loader with EAC disabled and offline play only.

## Balance source

`sheets/balance.json` is the source of truth. The builder prints and validates the complete balance and starting-loadout preflight before writing a package. The package changes only `EquipParamWeapon.param` rows 44010000 (Jar Cannon) and 53000000 (Ballista Bolt), and the 12 start/origin pairs in `CharaInitParam.param`. Every other Binder entry must retain its packed bytes.

## Known gap

The prototype is limited to vanilla firearm visuals, projectile behavior, and reload animation. It does not add Counter-Strike 2 assets, maps, or multiplayer.

## Safety

Use ModEngine 2 to launch Elden Ring offline with Easy Anti-Cheat disabled. Do not launch this modded installation online. Keep the provided unmodified backup of the existing save profile.
