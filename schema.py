# -*- coding: utf-8 -*-
"""
schema.py - Complete field map for The Bazaar's msgpack API protocol.

HOW TO UPDATE AFTER A PATCH
============================
1. Open ILSpy (ilspy\ILSpy.exe)
2. File -> Open, load these DLLs from the game folder:
       C:\Program Files (x86)\Steam\steamapps\common\The Bazaar\TheBazaar_Data\Managed\
           BazaarGameShared.dll
           BazaarGameClient.dll
           Assembly-CSharp.dll
           TheBazaarRuntime.dll
3. For each class listed below, search by class name in ILSpy search bar
   (set "Search for:" dropdown to "Types and Members")
4. Check if Key() indices or enum values have changed
5. Update the dicts in this file and the SOURCE PROVENANCE section

Discovered via ILSpy decompilation, game version: 1.0.10238-prod-windows-x64
DLL versions at time of discovery:
    BazaarGameShared.dll  v1.0.0.0  (.NETStandard v2.1)
    BazaarGameClient.dll  v1.0.0.0  (.NETStandard v2.1)
    Assembly-CSharp.dll   v0.0.0.0  (.NETStandard v2.1)
    TheBazaarRuntime.dll  v0.0.0.0  (.NETStandard v2.1)

Last verified: 2026-04-16

===================================================================
ENDPOINT
===================================================================
POST https://server.playthebazaar.com/commands
Content-Type: application/msgpack

Request headers (all observed in Fiddler capture):
    sid           - Session ID (also in Player.log as "Captured session id")
    rid           - Request sequence number (increments each command)
    aid           - Account ID (also in Player.log as "AccountId")
    uid           - Username
    Authorization - Bearer <JWT token>  (NOT in Player.log - unknown source)
    Cookie        - acaAffinity, __cflb (Cloudflare sticky session)

===================================================================
SOURCE PROVENANCE
===================================================================

--- REQUEST: INetCommand union type ---
SOURCE: BazaarGameShared.dll
CLASS:  MessagePack.GeneratedMessagePackResolver.BazaarGameShared.Infra.Commands.Commands
        -> inner class INetCommandFormatter
PATH:   BazaarGameShared.Infra.Commands namespace
SEARCH: "INetCommand" in ILSpy

Key evidence (from INetCommandFormatter.typeToKeyAndJumpMap):
    typeof(SelectItemCommand)       -> KeyValuePair(1, 0)
    typeof(MoveItemCommand)         -> KeyValuePair(2, 1)
    typeof(SelectSkillCommand)      -> KeyValuePair(3, 2)
    typeof(SelectEncounterCommand)  -> KeyValuePair(4, 3)
    typeof(RerollCommand)           -> KeyValuePair(5, 4)
    typeof(ExitCurrentStateCommand) -> KeyValuePair(6, 5)
    typeof(SellCardCommand)         -> KeyValuePair(7, 6)
    typeof(CommitToPedestalCommand) -> KeyValuePair(8, 7)
    typeof(InitializeRunCommand)    -> KeyValuePair(9, 8)
    typeof(CheatCommand)            -> KeyValuePair(10, 9)
    typeof(AbandonRunCommand)       -> KeyValuePair(11, 10)
Serialized as: writer.WriteArrayHeader(2); writer.WriteInt32(value2.Key); <command data>

--- RESPONSE ENVELOPE: NetMessageGameStateSync ---
SOURCE: BazaarGameShared.dll
CLASS:  BazaarGameShared.Infra.Messages.NetMessageGameStateSync
SEARCH: "NetMessageGameStateSync" in ILSpy

Decompiled class definition:
    [MessagePackObject(false)]
    public sealed record NetMessageGameStateSync(
        [property: Key(0)] GameStateSnapshotDTO Data
    ) : INetMessage
    {
        [Key(1)]
        public string MessageId { get; set; } = Nanoid.Generate(..., 3);
    }

Formatter (NetMessageGameStateSyncFormatter):
    writer.WriteArrayHeader(2);
    [0] -> GameStateSnapshotDTO (via resolver)
    [1] -> MessageId (string)

--- GameStateSnapshotDTO ---
SOURCE: BazaarGameShared.dll
CLASS:  BazaarGameShared.Infra.Messages.GameStateSnapshotDTO
SEARCH: "GameStateSnapshotDTO" in ILSpy

Decompiled class definition:
    [MessagePackObject(false)]
    public sealed class GameStateSnapshotDTO
    {
        [Key(0)] public RunSnapshotDTO Run = new RunSnapshotDTO();
        [Key(1)] public RunStateSnapshotDTO? CurrentState;
        [Key(2)] public PlayerSnapshotDTO Player = new PlayerSnapshotDTO();
        [Key(3)] public HashSet<CardSnapshotDTO> Cards = new HashSet<CardSnapshotDTO>();
    }

Formatter (GameStateSnapshotDTOFormatter):
    writer.WriteArrayHeader(4);
    case 0: Run
    case 1: CurrentState
    case 2: Player
    case 3: Cards

--- RunSnapshotDTO ---
SOURCE: BazaarGameShared.dll
CLASS:  BazaarGameShared.Infra.Messages.RunSnapshotDTO
SEARCH: "RunSnapshotDTO" in ILSpy

Decompiled class definition:
    [MessagePackObject(false)]
    public sealed class RunSnapshotDTO
    {
        [Key(0)] public Guid GameModeId = Guid.Empty;
        [Key(1)] public uint Day;
        [Key(2)] public uint Hour;
        [Key(3)] public uint Victories;
        [Key(4)] public uint Defeats;
        [Key(5)] public bool HasVisitedFates;
        [Key(6)] public string DataVersion = string.Empty;
    }

--- RunStateSnapshotDTO ---
SOURCE: BazaarGameShared.dll
CLASS:  BazaarGameShared.Infra.Messages.RunStateSnapshotDTO
SEARCH: "RunStateSnapshotDTO" in ILSpy

Decompiled class definition:
    [MessagePackObject(false)]
    public sealed class RunStateSnapshotDTO
    {
        [Key(0)] public ERunState StateName;
        [Key(1)] public string? CurrentEncounterId;
        [Key(2)] public string? Board;
        [Key(3)] public uint? RerollCost;
        [Key(4)] public uint? RerollsRemaining;
        [Key(5)] public List<string> SelectionSet = new List<string>();
        [Key(6)] public TSelectionContextRules? SelectionContextRules;
    }

NOTE: SelectionSet[5] contains instance IDs of cards currently offered
in the shop. Cross-reference with CardSnapshotDTO where Owner == null
to get TemplateIds.

--- PlayerSnapshotDTO ---
SOURCE: BazaarGameShared.dll
CLASS:  BazaarGameShared.Infra.Messages.PlayerSnapshotDTO
SEARCH: "PlayerSnapshotDTO" in ILSpy

Decompiled class definition:
    [MessagePackObject(false)]
    public sealed class PlayerSnapshotDTO
    {
        [Key(0)] public EHero Hero;
        [Key(1)] public Dictionary<EPlayerAttributeType, int> Attributes;
        [Key(2)] public ushort UnlockedSlots;
    }

NOTE: Attributes is a map keyed by EPlayerAttributeType enum int values.
Gold = key 4, Health = key 10, HealthMax = key 11.

--- CardSnapshotDTO ---
SOURCE: BazaarGameShared.dll
CLASS:  BazaarGameShared.Infra.Messages.CardSnapshotDTO
SEARCH: "CardSnapshotDTO" in ILSpy

Decompiled class definition:
    [MessagePackObject(false)]
    public sealed class CardSnapshotDTO
    {
        [Key(0)]  public string InstanceId = string.Empty;
        [Key(1)]  public Guid TemplateId = Guid.Empty;
        [Key(2)]  public Dictionary<ECardAttributeType, int> Attributes;
        [Key(3)]  public EEnchantmentType? Enchantment;
        [Key(4)]  public HashSet<EHero> Heroes;
        [Key(5)]  public HashSet<EHiddenTag> HiddenTags;
        [Key(6)]  public HashSet<ECardTag> Tags;
        [Key(7)]  public ETier Tier;
        [Key(8)]  public ECardType Type;
        [Key(9)]  public ECardSize Size = ECardSize.Medium;
        [Key(10)] public ECombatantId? Owner;
        [Key(11)] public EContainerSocketId? Socket;
        [Key(12)] public EInventorySection? Section;
    }

Card categorization logic (from GameStateSnapshotDTO computed properties):
    Offered in shop : Owner == null AND TemplateId != CurrentEncounterId
    Player hand     : Owner == ECombatantId.Player AND Section == EInventorySection.Hand
    Player stash    : Owner == ECombatantId.Player AND Section == EInventorySection.Stash
    Player skills   : Owner == ECombatantId.Player AND !Section.HasValue

--- ERunState ---
SOURCE: BazaarGameShared.dll
CLASS:  BazaarGameShared.Domain.Runs.ERunState
SEARCH: "ERunState" in ILSpy

Decompiled enum:
    public enum ERunState
    {
        Choice, Combat, Encounter, EndRunDefeat, EndRunVictory,
        LevelUp, Loot, NewRun, Pedestal, PVPCombat, Shutdown
    }

--- EHero ---
SOURCE: BazaarGameShared.dll
CLASS:  BazaarGameShared.Domain.Core.Types.EHero
SEARCH: "EHero" in ILSpy

--- EPlayerAttributeType ---
SOURCE: BazaarGameShared.dll
CLASS:  BazaarGameShared.Domain.Core.Types.EPlayerAttributeType
SEARCH: "EPlayerAttributeType" in ILSpy

Decompiled enum (all values):
    Burn=0, CritChance=1, DamageCrit=2, Experience=3, Gold=4,
    Income=5, Joy=6, JoyCrit=8, Prestige=9, Health=10, HealthMax=11,
    HealthRegen=12, HealAmount=13, HealCrit=14, Level=15, Poison=16,
    RerollCostModifier=17, Shield=19, ShieldCrit=21,
    FlatDamageReduction=22, PercentDamageReduction=23,
    Custom_0=24 ... Custom_9=33,
    Rage=34, RageMax=35, Enraged=36, EnragedDuration=37,
    EnragedDurationMax=38

--- ECardType ---
SOURCE: BazaarGameShared.dll
CLASS:  BazaarGameShared.Domain.Core.Types.ECardType
SEARCH: "ECardType" in ILSpy

--- ECardSize ---
SOURCE: BazaarGameShared.dll
CLASS:  BazaarGameShared.Domain.Core.Types.ECardSize
SEARCH: "ECardSize" in ILSpy

--- ETier ---
SOURCE: BazaarGameShared.dll
CLASS:  BazaarGameShared.Domain.Core.Types.ETier
SEARCH: "ETier" in ILSpy

--- ECombatantId ---
SOURCE: BazaarGameShared.dll
CLASS:  BazaarGameShared.Domain.Core.Types.ECombatantId
SEARCH: "ECombatantId" in ILSpy

--- EInventorySection ---
SOURCE: BazaarGameShared.dll
CLASS:  BazaarGameShared.Domain.Core.Types.EInventorySection
SEARCH: "EInventorySection" in ILSpy

--- GameStateHandler (how server responses are consumed) ---
SOURCE: TheBazaarRuntime.dll (compiled from Assembly-CSharp sources)
CLASS:  TheBazaar.GameStateHandler
SEARCH: "GameStateHandler" in ILSpy

Key finding: The game calls Data.UpdateFromStateSync(message) on every
NetMessageGameStateSync received, then syncs the board UI.
This confirms every command response is a full game state snapshot,
not a delta — we always get the complete current state.

--- GameStateMetadata (server-side, not in DTO) ---
SOURCE: BazaarBattleService.dll
CLASS:  BazaarBattleService.GameStateMetadata
SEARCH: "GameStateMetadata" in ILSpy

NOTE: This class exists on the server side and contains PlayerWonCombat
and other fields NOT transmitted to the client in the DTO. PvP outcome
must be inferred from RunSnapshotDTO.Victories/Defeats delta between
successive responses instead.

Fields of interest (server-side only, for reference):
    PlayerWonCombat          bool
    currentPvpOpponent       Guid?
    WasInActiveCombat        bool
    LastHourDealtCards       List<Guid>  <- dealt this shop (template IDs)
    DealtCardForReRollExclusion List<Guid>
    EncounterRerollsRemaining int
    EncounterRerolls          int

===================================================================
OPEN QUESTIONS (as of 2026-04-16)
===================================================================
1. Bearer token source
   - Not in Player.log, not in AppData files
   - Likely from Steam GetAuthSessionTicket exchanged for JWT
   - Search "SteamAuth" or "GetAuthTicket" in Assembly-CSharp.dll
   - Also check BazaarGameClient.dll -> search "token" or "jwt"

2. Attribute map key encoding discrepancy
   - Sample capture showed key 40 -> value 38 in PlayerSnapshotDTO[1]
   - EPlayerAttributeType.Gold = 4, not 40
   - Possible causes: map uses different int encoding, or offset exists
   - Needs live capture with known gold amount to verify
   - Check ECardAttributeType enum (separate from EPlayerAttributeType)

3. EContainerSocketId values
   - Socket field [11] in CardSnapshotDTO uses this enum
   - Not yet decompiled - search "EContainerSocketId" in ILSpy
   - Needed to map board positions accurately

4. TSelectionContextRules
   - Field [6] in RunStateSnapshotDTO
   - Not yet decompiled - search "TSelectionContextRules" in ILSpy
   - May contain additional shop context (free items, forced picks etc)

5. ECardAttributeType
   - Field [2] in CardSnapshotDTO uses this (different from EPlayerAttributeType)
   - Not yet decompiled - search "ECardAttributeType" in ILSpy
   - Needed to read per-card stats (damage, speed, cooldown etc)
"""

# ===================================================================
# PYTHON REFERENCE DICTS
# ===================================================================
# Each dict maps the integer value used in msgpack to a human name.
# Source class and Key() index noted for each for patch verification.

# BazaarGameShared.Domain.Runs.ERunState
# Used in: RunStateSnapshotDTO[Key(0)]
E_RUN_STATE = {
    0:  "Choice",
    1:  "Combat",
    2:  "Encounter",
    3:  "EndRunDefeat",
    4:  "EndRunVictory",
    5:  "LevelUp",
    6:  "Loot",
    7:  "NewRun",
    8:  "Pedestal",
    9:  "PVPCombat",
    10: "Shutdown",
}

# BazaarGameShared.Domain.Core.Types.EHero
# Used in: PlayerSnapshotDTO[Key(0)], CardSnapshotDTO[Key(4)]
E_HERO = {
    0: "Common",
    1: "Pygmalien",
    2: "Vanessa",
    3: "Dooley",
    4: "Jules",
    5: "Stelle",
    6: "Mak",
    7: "Karnok",   # confirmed from live Mono capture while playing Karnok
}

# BazaarGameShared.Domain.Core.Types.EPlayerAttributeType
# Used in: PlayerSnapshotDTO[Key(1)] as dict keys
E_PLAYER_ATTRIBUTE = {
    0:  "Burn",
    1:  "CritChance",
    2:  "DamageCrit",
    3:  "Experience",
    4:  "Gold",           # current gold
    5:  "Income",
    6:  "Joy",
    8:  "JoyCrit",
    9:  "Prestige",
    10: "Health",         # current HP
    11: "HealthMax",      # max HP
    12: "HealthRegen",
    13: "HealAmount",
    14: "HealCrit",
    15: "Level",
    16: "Poison",
    17: "RerollCostModifier",
    19: "Shield",
    21: "ShieldCrit",
    22: "FlatDamageReduction",
    23: "PercentDamageReduction",
    24: "Custom_0",
    25: "Custom_1",
    26: "Custom_2",
    27: "Custom_3",
    28: "Custom_4",
    29: "Custom_5",
    30: "Custom_6",
    31: "Custom_7",
    32: "Custom_8",
    33: "Custom_9",
    34: "Rage",
    35: "RageMax",
    36: "Enraged",
    37: "EnragedDuration",
    38: "EnragedDurationMax",
}

# BazaarGameShared.Domain.Core.Types.ECardType
# Used in: CardSnapshotDTO[Key(8)]
E_CARD_TYPE = {
    0: "Item",
    1: "Skill",
    2: "Companion",
    3: "SocketEffect",
    4: "Encounter",
}

# BazaarGameShared.Domain.Core.Types.ECardSize
# Used in: CardSnapshotDTO[Key(9)]
E_CARD_SIZE = {
    0: "Small",
    1: "Medium",
    2: "Large",
}

# BazaarGameShared.Domain.Core.Types.ETier
# Used in: CardSnapshotDTO[Key(7)]
E_TIER = {
    0: "Bronze",
    1: "Silver",
    2: "Gold",
    3: "Diamond",
    4: "Legendary",
}

# BazaarGameShared.Domain.Core.Types.ECombatantId
# Used in: CardSnapshotDTO[Key(10)] - null means offered in shop
E_COMBATANT = {
    0: "Player",
    1: "Opponent",
}

# BazaarGameShared.Domain.Core.Types.EInventorySection
# Used in: CardSnapshotDTO[Key(12)] - null means skill (no section)
E_INVENTORY_SECTION = {
    0: "Hand",
    1: "Stash",
}

# BazaarGameShared.Domain.Core.Types.EPlayMode
# Used in: InitializeRunCommand[1]
E_PLAY_MODE = {
    0: "Unranked",
    1: "Ranked",
}

# Command type ID -> class name
# Source: INetCommandFormatter.typeToKeyAndJumpMap in BazaarGameShared.dll
# Each command is serialized as [type_id, command_data_array]
COMMAND_TYPES = {
    1:  "SelectItemCommand",        # [instanceId, targetSockets, section?]
    2:  "MoveItemCommand",          # [instanceId, targetSockets, section]
    3:  "SelectSkillCommand",       # [instanceId]
    4:  "SelectEncounterCommand",   # [instanceId]
    5:  "RerollCommand",            # []
    6:  "ExitCurrentStateCommand",  # []
    7:  "SellCardCommand",          # [instanceId]
    8:  "CommitToPedestalCommand",  # [instanceId]
    9:  "InitializeRunCommand",     # [gameModeId?, playMode, selectedHero]
    10: "CheatCommand",             # [args]
    11: "AbandonRunCommand",        # []
}

# DTO field index -> name mappings
# Source: [Key(N)] annotations on each class in BazaarGameShared.dll

# BazaarGameShared.Infra.Messages.NetMessageGameStateSync
NET_MESSAGE_FIELDS = {
    0: "Data",       # GameStateSnapshotDTO
    1: "MessageId",  # string (3-char nanoid)
}

# BazaarGameShared.Infra.Messages.GameStateSnapshotDTO
GAME_STATE_SNAPSHOT_FIELDS = {
    0: "Run",           # RunSnapshotDTO
    1: "CurrentState",  # RunStateSnapshotDTO (nullable)
    2: "Player",        # PlayerSnapshotDTO
    3: "Cards",         # HashSet<CardSnapshotDTO>
}

# BazaarGameShared.Infra.Messages.RunSnapshotDTO
RUN_SNAPSHOT_FIELDS = {
    0: "GameModeId",      # Guid
    1: "Day",             # uint
    2: "Hour",            # uint
    3: "Victories",       # uint - PvP wins this run
    4: "Defeats",         # uint - PvP losses this run
    5: "HasVisitedFates", # bool
    6: "DataVersion",     # string
}

# BazaarGameShared.Infra.Messages.RunStateSnapshotDTO
RUN_STATE_SNAPSHOT_FIELDS = {
    0: "StateName",            # ERunState int
    1: "CurrentEncounterId",   # string? (instance ID)
    2: "Board",                # string? (serialized board)
    3: "RerollCost",           # uint?
    4: "RerollsRemaining",     # uint?
    5: "SelectionSet",         # List<string> - offered instance IDs
    6: "SelectionContextRules",# TSelectionContextRules? (not yet decoded)
}

# BazaarGameShared.Infra.Messages.PlayerSnapshotDTO
PLAYER_SNAPSHOT_FIELDS = {
    0: "Hero",          # EHero int
    1: "Attributes",    # Dictionary<EPlayerAttributeType, int>
    2: "UnlockedSlots", # ushort
}

# BazaarGameShared.Infra.Messages.CardSnapshotDTO
CARD_SNAPSHOT_FIELDS = {
    0:  "InstanceId",   # string e.g. "itm_1fX36FE"
    1:  "TemplateId",   # Guid -> resolve via card_cache table
    2:  "Attributes",   # Dictionary<ECardAttributeType, int> (not yet decoded)
    3:  "Enchantment",  # EEnchantmentType? int
    4:  "Heroes",       # HashSet<EHero>
    5:  "HiddenTags",   # HashSet<EHiddenTag>
    6:  "Tags",         # HashSet<ECardTag>
    7:  "Tier",         # ETier int
    8:  "Type",         # ECardType int
    9:  "Size",         # ECardSize int
    10: "Owner",        # ECombatantId? int - null = offered in shop
    11: "Socket",       # EContainerSocketId? int (not yet decoded)
    12: "Section",      # EInventorySection? int - null = skill
}

# Instance ID prefix -> card type (observed in Player.log and API captures)
INSTANCE_ID_PREFIXES = {
    "itm_": "Item",
    "skl_": "Skill",
    "com_": "Companion",
    "enc_": "Encounter",
    "ste_": "StashEncounter",
    "ped_": "Pedestal",
}


if __name__ == "__main__":
    print("Bazaar API Schema Reference")
    print(f"  Game version : 1.0.10238-prod-windows-x64")
    print(f"  Verified     : 2026-04-16")
    print(f"  Run states   : {len(E_RUN_STATE)}")
    print(f"  Heroes       : {len(E_HERO)}")
    print(f"  Player attrs : {len(E_PLAYER_ATTRIBUTE)}")
    print(f"  Card types   : {len(E_CARD_TYPE)}")
    print(f"  Command types: {len(COMMAND_TYPES)}")
    print(f"  Card fields  : {len(CARD_SNAPSHOT_FIELDS)}")
    print()
    print("Open questions requiring further ILSpy investigation:")
    print("  - EContainerSocketId  (CardSnapshotDTO[11])")
    print("  - ECardAttributeType  (CardSnapshotDTO[2])")
    print("  - TSelectionContextRules (RunStateSnapshotDTO[6])")
    print("  - Bearer token generation (Assembly-CSharp.dll -> SteamAuth)")
