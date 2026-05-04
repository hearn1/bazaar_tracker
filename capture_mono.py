# -*- coding: utf-8 -*-
"""
capture_mono.py â€” Frida Mono hook for The Bazaar's managed GameStateHandler.

WHY THIS APPROACH
=================
All prior capture attempts hit the same wall: Unity's bundled TLS library
doesn't export SSL_read/SSL_write, the game uses IPv6 + Cloudflare, and
the localhost internal tunnel means Winsock/Schannel hooks only see
encrypted traffic on the wrong side of the pipe.

This script skips the network layer entirely. The Bazaar runs on Unity
with Mono (confirmed: mono-2.0-bdwgc.dll is loaded). We call the Mono C
API directly via NativeFunction to find the managed GameStateHandler class,
then hook the method that processes NetMessageGameStateSync. When the game
receives a server response, our hook fires with the fully deserialized
GameStateSnapshotDTO already in managed memory. We read its fields and
send structured JSON back to Python.

No proxy, no cert, no TLS decryption, no kernel driver, no admin rights.

REQUIREMENTS
============
  pip install frida frida-tools
  The game must be running (or use --wait).

USAGE
=====
  python capture_mono.py                     # attach to running game
  python capture_mono.py --wait              # wait for game to launch
  python capture_mono.py --log               # save captures to disk
  python capture_mono.py --log --db          # save + write to SQLite
"""

import argparse
import datetime
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

CAPTURES_DIR = Path(__file__).parent / "captures"

# Frida Mono agent - uses native Mono C API via NativeFunction
FRIDA_MONO_AGENT = r"""
'use strict';

const E_RUN_STATE = {0:"Choice",1:"Combat",2:"Encounter",3:"EndRunDefeat",4:"EndRunVictory",5:"LevelUp",6:"Loot",7:"NewRun",8:"Pedestal",9:"PVPCombat",10:"Shutdown"};
const E_HERO = {0:"Common",1:"Pygmalien",2:"Vanessa",3:"Dooley",4:"Jules",5:"Stelle",6:"Mak",7:"Karnok"};
const E_PLAYER_ATTRIBUTE = {0:"Burn",1:"CritChance",2:"DamageCrit",3:"Experience",4:"Gold",5:"Income",6:"Joy",8:"JoyCrit",9:"Prestige",10:"Health",11:"HealthMax",12:"HealthRegen",13:"HealAmount",14:"HealCrit",15:"Level",16:"Poison",17:"RerollCostModifier",19:"Shield",21:"ShieldCrit",22:"FlatDamageReduction",23:"PercentDamageReduction",24:"Custom_0",25:"Custom_1",26:"Custom_2",27:"Custom_3",28:"Custom_4",29:"Custom_5",30:"Custom_6",31:"Custom_7",32:"Custom_8",33:"Custom_9",34:"Rage",35:"RageMax",36:"Enraged",37:"EnragedDuration",38:"EnragedDurationMax"};
const E_CARD_TYPE = {0:"Item",1:"Skill",2:"Companion",3:"SocketEffect",4:"Encounter"};
const E_CARD_SIZE = {0:"Small",1:"Medium",2:"Large"};
const E_TIER = {0:"Bronze",1:"Silver",2:"Gold",3:"Diamond",4:"Legendary"};
const E_COMBATANT = {0:"Player",1:"Opponent"};
const E_INVENTORY_SECTION = {0:"Hand",1:"Stash"};
const KEEP_PLAYER_ATTR_IDS = {4:true,9:true,10:true,11:true,15:true};
const KEEP_PLAYER_ATTR_COUNT = 5;
const COMMAND_KIND = {
    SelectItemCommand: "buy",
    // MoveItemCommand: "move",
    SelectSkillCommand: "skill_select",
    SelectEncounterCommand: "event_choice",
    RerollCommand: "reroll",
    SellCardCommand: "sell",
    CommitToPedestalCommand: "pedestal_commit",
    ExitCurrentStateCommand: "exit_state"
};
// Re-enabled from W/L-only scope-down: template variables restored
const FULL_DELTA_CARDS = __FULL_DELTA_CARDS__;
const ENABLE_PROBES = __ENABLE_PROBES__;
const ENABLE_BROAD_HOOKS = __ENABLE_BROAD_HOOKS__;
// Re-enabled from W/L-only scope-down: template variables restored
const DELTA_PLAYER_ATTRS = __DELTA_PLAYER_ATTRS__;
// Re-enabled from W/L-only scope-down: template variables restored
const ACTION_EVENT_CARDS = __ACTION_EVENT_CARDS__;
const CAPTURE_OPPONENT_BOARD = __CAPTURE_OPPONENT_BOARD__;
const VERBOSE_HOOK_CALLS = __VERBOSE_HOOK_CALLS__;
// Re-enabled from W/L-only scope-down (F1): card state sets for gating deferred card reads
const HEAVY_CARD_STATES = {Choice:true,Loot:true,LevelUp:true,Pedestal:true,EndRunVictory:true,EndRunDefeat:true};
const ACTION_CARD_STATES = {Choice:true,Encounter:true,Loot:true,LevelUp:true,Pedestal:true,Combat:true,PVPCombat:true,Replay:true,EndRunVictory:true,EndRunDefeat:true};
const ACTION_TEMPLATE_EVENT_STATES = {Choice:true,Encounter:true,Loot:true,LevelUp:true,Pedestal:true,EndRunVictory:true,EndRunDefeat:true};
// SCOPED OUT: DISABLE_DICTIONARY_PROBING / MAX_INLINE_CARD_COUNT / INLINE_CARD_STATES unused in W/L-only mode
// const DISABLE_DICTIONARY_PROBING = true;
// const MAX_INLINE_CARD_COUNT = 4;
// const INLINE_CARD_STATES = {Loot:true,LevelUp:true,Choice:true,Encounter:true,Pedestal:true,EndRunVictory:true,EndRunDefeat:true};
const DISABLE_DICTIONARY_PROBING = false;
const MAX_INLINE_CARD_COUNT = 4;
const INLINE_CARD_STATES = {Loot:true,LevelUp:true,Choice:true,Encounter:true,Pedestal:true,EndRunVictory:true,EndRunDefeat:true};
const SLOW_HOOK_MS = 8;
const ATTRS_STAT_REPORT_INTERVAL_MS = 30000;
// QW9: Fast GameSim path — merges lean+payload into single pass, batches field
// reads, relaxes pointer validation inside known-good object graphs, caches
// SelectionSet by pointer identity, and throttles player attrs to state changes.
// Set to false to revert to the legacy double-read path.
const FAST_GAMESIM_PATH = true;
// QW9: Throttled sync attrs — only read player attributes when state changes
// or on the first snapshot. Eliminates the 87% deferred-attrs failure cascade.
const ATTRS_THROTTLE_ON_STATE_CHANGE = true;
// QW9: Minimum interval between sync attr reads (ms) as a safety valve.
const ATTRS_SYNC_MIN_INTERVAL_MS = 2000;
let _lastAttrsSyncMs = 0;
let _lastAttrsSyncState = null;
let _attrsSyncThrottledCount = 0;
let _attrsSyncReadCount = 0;
let _attrsSyncEmptyCount = 0;
// QW9: Cache last successful attrs result — readEnumIntDict fails ~90% of the
// time (the managed dict entries array is often mid-update when our hook fires).
// Cache the last good result and reuse it on failure. Gold/HP change infrequently
// enough that stale-by-one-snapshot is acceptable for the overlay.
let _lastGoodAttrs = null;
let _attrsFromCacheCount = 0;
// QW10: Cached dictionary layout for player attrs. Populated on first successful
// readEnumIntDict call, then _fastReadPlayerAttrs uses pure direct memory reads
// with ZERO NativeFunction calls. This eliminates the ~50-100ms readEnumIntDict cost.
let _playerAttrsDictLayout = null; // {entriesOff, countOff, entrySize, hashOff, keyOff, valueOff}
let _fastAttrsReadCount = 0;
let _fastAttrsFailCount = 0;
// QW9: SelectionSet cache (content-hash, see _readSelectionSetCached)
let _lastSelectionSetResult = [];
let _selectionSetCacheHits = 0;
let _selectionSetCacheMisses = 0;
// QW9: Batch field reader offset cache — keyed by className, maps field name to {offset, type}
const _batchFieldOffsetCache = {};
// QW10: Cache DataVersion string (static per run, avoids mono_string_to_utf8 on every hook)
let _cachedDataVersion = null;
let snapshotCounter = 0;
// Deferred Player.Attributes: enumerate off the game thread via setImmediate.
// If the deferred decode returns empty or throws, force a sync read on the next
// eligible hook so we recover the data at the cost of one stutter.
let _pendingSyncAttrsRead = false;
let _deferredAttrsSuccessCount = 0;
let _deferredAttrsFailureCount = 0;
let _syncAttrsFallbackCount = 0;
let _lastAttrsStatReportMs = 0;
const captureCallCounts = {};
const hookedCode = {};
const probeLogCounts = {};
const commandProbeLogCounts = {};
const argLogCounts = {};
const seenMessageIds = {};
const seenMessageOrder = [];
const MAX_SEEN_MESSAGE_IDS = 512;
const seenCommandKeys = {};
const seenCommandOrder = [];
const MAX_SEEN_COMMAND_KEYS = 512;
let commandCounter = 0;

const mono = Process.getModuleByName('mono-2.0-bdwgc.dll');
function monoExport(name, ret, args) {
    const addr = mono.getExportByName(name);
    if (!addr) { send({type:'error',msg:'Export not found: '+name}); return null; }
    return new NativeFunction(addr, ret, args);
}

function monoOptionalExport(name, ret, args) {
    try {
        const addr = mono.getExportByName(name);
        return addr ? new NativeFunction(addr, ret, args) : null;
    } catch (e) {
        return null;
    }
}

const mono_get_root_domain = monoExport('mono_get_root_domain','pointer',[]);
const mono_thread_attach = monoExport('mono_thread_attach','pointer',['pointer']);
const mono_assembly_foreach = monoExport('mono_assembly_foreach','void',['pointer','pointer']);
const mono_assembly_get_image = monoExport('mono_assembly_get_image','pointer',['pointer']);
const mono_image_get_name = monoExport('mono_image_get_name','pointer',['pointer']);
const mono_image_get_table_rows = monoExport('mono_image_get_table_rows','int',['pointer','int']);
const mono_class_from_name = monoExport('mono_class_from_name','pointer',['pointer','pointer','pointer']);
const mono_class_get = monoExport('mono_class_get','pointer',['pointer','uint32']);
const mono_class_get_name = monoExport('mono_class_get_name','pointer',['pointer']);
const mono_class_get_namespace = monoExport('mono_class_get_namespace','pointer',['pointer']);
const mono_class_get_methods = monoExport('mono_class_get_methods','pointer',['pointer','pointer']);
const mono_class_get_fields = monoExport('mono_class_get_fields','pointer',['pointer','pointer']);
const mono_class_get_method_from_name = monoExport('mono_class_get_method_from_name','pointer',['pointer','pointer','int']);
const mono_method_get_name = monoExport('mono_method_get_name','pointer',['pointer']);
const mono_method_signature = monoExport('mono_method_signature','pointer',['pointer']);
const mono_compile_method = monoExport('mono_compile_method','pointer',['pointer']);
const mono_signature_get_param_count = monoExport('mono_signature_get_param_count','uint32',['pointer']);
const mono_signature_get_params = monoExport('mono_signature_get_params','pointer',['pointer','pointer']);
const mono_signature_get_return_type = monoExport('mono_signature_get_return_type','pointer',['pointer']);
const mono_field_get_name = monoExport('mono_field_get_name','pointer',['pointer']);
const mono_field_get_type = monoExport('mono_field_get_type','pointer',['pointer']);
const mono_field_get_value = monoExport('mono_field_get_value','void',['pointer','pointer','pointer']);
const mono_field_get_offset = monoExport('mono_field_get_offset','int',['pointer']);
const mono_object_get_class = monoExport('mono_object_get_class','pointer',['pointer']);
const mono_type_get_name = monoExport('mono_type_get_name','pointer',['pointer']);
const mono_string_to_utf8 = monoExport('mono_string_to_utf8','pointer',['pointer']);
const mono_free = monoExport('mono_free','void',['pointer']);
const mono_class_get_element_class = monoOptionalExport('mono_class_get_element_class','pointer',['pointer']);
const mono_class_value_size = monoOptionalExport('mono_class_value_size','int',['pointer','pointer']);

const domain = mono_get_root_domain();
if (!domain.isNull()) { mono_thread_attach(domain); send({type:'info',msg:'Attached to Mono domain'}); }

const assemblies = [];
const asmCb = new NativeCallback(function(a,u){assemblies.push(a);},'void',['pointer','pointer']);
mono_assembly_foreach(asmCb, ptr(0));
send({type:'info',msg:'Found '+assemblies.length+' assemblies'});

const imageMap = {};
for (const asm of assemblies) {
    const img = mono_assembly_get_image(asm);
    if (img.isNull()) continue;
    const np = mono_image_get_name(img);
    if (!np.isNull()) imageMap[np.readUtf8String()] = img;
}
send({type:'info',msg:'Images: '+Object.keys(imageMap).join(', ')});

function findClass(ns, cls) {
    const nsP = Memory.allocUtf8String(ns), clsP = Memory.allocUtf8String(cls);
    for (const n of ['TheBazaarRuntime','Assembly-CSharp','BazaarGameShared','BazaarGameClient']) {
        if (!imageMap[n]) continue;
        const k = mono_class_from_name(imageMap[n], nsP, clsP);
        if (!k.isNull()) { send({type:'info',msg:'Found '+ns+'.'+cls+' in '+n}); return k; }
    }
    for (const [n,img] of Object.entries(imageMap)) {
        const k = mono_class_from_name(img, nsP, clsP);
        if (!k.isNull()) { send({type:'info',msg:'Found '+ns+'.'+cls+' in '+n}); return k; }
    }
    return null;
}

function getMethods(klass) {
    const methods = [], iter = Memory.alloc(Process.pointerSize);
    iter.writePointer(ptr(0));
    while (true) {
        const m = mono_class_get_methods(klass, iter);
        if (m.isNull()) break;
        const np = mono_method_get_name(m);
        const sig = getMethodSignature(m);
        methods.push({
            ptr:m,
            name: np.isNull()?'?':np.readUtf8String(),
            paramCount: sig.paramCount,
            params: sig.params,
            ret: sig.ret,
        });
    }
    return methods;
}

function cloneMethodWithMeta(method, extra) {
    return Object.assign({}, method, extra || {});
}

function getFields(klass) {
    const fields = [], iter = Memory.alloc(Process.pointerSize);
    iter.writePointer(ptr(0));
    while (true) {
        const f = mono_class_get_fields(klass, iter);
        if (f.isNull()) break;
        const np = mono_field_get_name(f);
        fields.push({
            ptr:f,
            name: np.isNull()?'?':np.readUtf8String(),
            offset: mono_field_get_offset(f),
            type: getFieldTypeName(f),
        });
    }
    return fields;
}

function readOwnedUtf8(ptrValue) {
    if (!ptrValue || ptrValue.isNull()) return null;
    const s = ptrValue.readUtf8String();
    mono_free(ptrValue);
    return s;
}

function classFullName(klass) {
    if (!klass || klass.isNull()) return null;
    const nsPtr = mono_class_get_namespace(klass);
    const namePtr = mono_class_get_name(klass);
    if (namePtr.isNull()) return null;
    const ns = nsPtr.isNull() ? '' : nsPtr.readUtf8String();
    const name = namePtr.readUtf8String();
    return ns ? (ns + '.' + name) : name;
}

function getTypeName(typePtr) {
    try {
        if (!typePtr || typePtr.isNull()) return null;
        return readOwnedUtf8(mono_type_get_name(typePtr));
    } catch (e) {
        return null;
    }
}

function getFieldTypeName(fieldPtr) {
    try {
        return getTypeName(mono_field_get_type(fieldPtr));
    } catch (e) {
        return null;
    }
}

function getMethodSignature(methodPtr) {
    try {
        const sig = mono_method_signature(methodPtr);
        if (!sig || sig.isNull()) return {paramCount: 0, params: [], ret: '?'};
        const paramCount = mono_signature_get_param_count(sig);
        const iter = Memory.alloc(Process.pointerSize);
        iter.writePointer(ptr(0));
        const params = [];
        for (let i = 0; i < paramCount; i++) {
            params.push(getTypeName(mono_signature_get_params(sig, iter)) || '?');
        }
        return {
            paramCount: paramCount,
            params: params,
            ret: getTypeName(mono_signature_get_return_type(sig)) || 'void',
        };
    } catch (e) {
        return {paramCount: 0, params: [], ret: '?'};
    }
}

function formatMethod(method) {
    return method.name + '(' + method.params.join(', ') + ') -> ' + method.ret;
}

function findFieldInfo(classKey, names) {
    const info = fieldInfoCache[classKey];
    if (!info) return null;
    for (const name of names) {
        if (info[name]) return info[name];
    }
    return null;
}

function getDynamicFieldsForKlass(klass) {
    const className = classFullName(klass);
    if (!className) return [];
    if (!dynamicFieldInfoCache[className]) {
        dynamicFieldInfoCache[className] = getFields(klass);
    }
    return dynamicFieldInfoCache[className];
}

function enumerateClassesInImage(image, assemblyName) {
    const classes = [];
    try {
        const MONO_TABLE_TYPEDEF = 2;
        const rows = mono_image_get_table_rows(image, MONO_TABLE_TYPEDEF);
        for (let i = 1; i <= rows; i++) {
            try {
                const token = (0x02000000 | i) >>> 0;
                const klass = mono_class_get(image, token);
                if (!klass || klass.isNull()) continue;
                const fullName = classFullName(klass);
                if (!fullName) continue;
                classes.push({
                    klass: klass,
                    assembly: assemblyName,
                    fullName: fullName,
                    name: fullName.split('.').pop(),
                });
            } catch (e) {}
        }
    } catch (e) {
        send({type:'debug',msg:'enumerateClassesInImage '+assemblyName+': '+e});
    }
    return classes;
}

function readObjectFieldByInfo(objPtr, field) {
    try {
        if (!objPtr || objPtr.isNull() || !field || !field.ptr) return null;
        const buf = Memory.alloc(Process.pointerSize);
        buf.writePointer(ptr(0));
        mono_field_get_value(objPtr, field.ptr, buf);
        const value = buf.readPointer();
        return isReadablePointer(value) ? value : null;
    } catch (e) {
        return null;
    }
}

function readObjectField(objPtr, classKey, fieldNames) {
    try {
        if (!objPtr || objPtr.isNull()) return null;
        const field = findFieldInfo(classKey, Array.isArray(fieldNames) ? fieldNames : [fieldNames]);
        if (!field) return null;
        return readObjectFieldByInfo(objPtr, field);
    } catch (e) {
        send({type:'debug',msg:'readObjectField '+classKey+'.'+fieldNames+': '+e});
        return null;
    }
}

function logNullField(key, detail) {
    fieldNullLogCounts[key] = (fieldNullLogCounts[key] || 0) + 1;
    if (fieldNullLogCounts[key] <= 5) {
        send({type:'debug',msg:key+' '+detail});
    }
}

function isKnownBadPointer(ptrValue) {
    try {
        if (!ptrValue || ptrValue.isNull()) return true;
        const s = ptrValue.toString().toLowerCase();
        return s === '0xffffffffffffffff' ||
               s === '0xcccccccccccccccc' ||
               s === '0xcdcdcdcdcdcdcdcd' ||
               s === '0xdddddddddddddddd' ||
               s === '0xfeeefeeefeeefeee';
    } catch (e) {
        return true;
    }
}

// QW2: Persist range cache across hook calls â€” managed heap pages are stable during a running game.
// Cache is no longer cleared per invocation; instead, a simple size cap evicts the oldest half
// when entries exceed 256. The existing try/catch in isReadableAddress handles unmapped-page edge cases.
const _rangeCache = {};
const _rangeCacheKeys = []; // insertion-order key list for LRU-style eviction
const _RANGE_CACHE_MAX = 256;
let _rangeCacheHits = 0;
let _rangeCacheMisses = 0;
function resetRangeCache() {
    // QW2: no-op â€” cache is now persistent across hook calls. Kept to avoid ReferenceErrors.
}

function isReadableAddress(ptrValue, size) {
    try {
        if (isKnownBadPointer(ptrValue)) return false;
        // Cache lookup on page-aligned address (4KB pages)
        const pageKey = ptrValue.and(ptr('0xFFFFFFFFFFFFF000')).toString();
        let range;
        if (pageKey in _rangeCache) {
            range = _rangeCache[pageKey];
            _rangeCacheHits++;
        } else {
            range = Process.findRangeByAddress(ptrValue);
            // QW2: cap cache at _RANGE_CACHE_MAX â€” evict oldest half when full
            if (_rangeCacheKeys.length >= _RANGE_CACHE_MAX) {
                const evict = _rangeCacheKeys.splice(0, _RANGE_CACHE_MAX >> 1);
                for (const k of evict) delete _rangeCache[k];
            }
            _rangeCache[pageKey] = range;
            _rangeCacheKeys.push(pageKey);
            _rangeCacheMisses++;
        }
        if (!range || String(range.protection || '').indexOf('r') === -1) return false;
        const bytes = size || 1;
        if (bytes <= 1) return true;
        const maxStart = range.base.add(Math.max(0, range.size - bytes));
        return ptrValue.compare(maxStart) <= 0;
    } catch (e) {
        return false;
    }
}

function isReadablePointer(ptrValue) {
    return isReadableAddress(ptrValue, 1);
}

function safeReadPointer(basePtr, offset) {
    try {
        if (!isReadablePointer(basePtr)) return null;
        const addr = offset === undefined ? basePtr : basePtr.add(offset);
        if (!isReadableAddress(addr, Process.pointerSize)) return null;
        const value = addr.readPointer();
        return isReadablePointer(value) ? value : null;
    } catch (e) {
        return null;
    }
}

function readMonoString(strObj) {
    if (!isReadablePointer(strObj)) return null;
    try {
        const p = mono_string_to_utf8(strObj);
        if (!p || p.isNull()) return null;
        const s = p.readUtf8String();
        mono_free(p);
        return s;
    } catch (e) {
        return null;
    }
}

// QW10: Direct managed string reader — reads UTF-16 chars from the Mono string's
// internal char buffer with ZERO NativeFunction calls. MonoString layout:
//   offset 0: MonoObject header (vtable ptr)
//   offset 8: int32 length (char count)
//   offset 12: padding (4 bytes on 64-bit)
//   offset 16: char[] chars (UTF-16LE, 2 bytes per char)  — NOTE: may be 12 on some builds
// Falls back to readMonoString on failure.
const MONO_STRING_LENGTH_OFFSET = 8;
const MONO_STRING_CHARS_OFFSET = 12; // Will try 12 first (common), then 16
let _monoStringCharsOffset = null; // auto-detected on first call
function _directReadMonoString(strPtr) {
    try {
        if (!strPtr || strPtr.isNull()) return null;
        const len = strPtr.add(MONO_STRING_LENGTH_OFFSET).readS32();
        if (len <= 0 || len > 4096) return null;
        // Auto-detect chars offset on first call by verifying against mono_string_to_utf8
        if (_monoStringCharsOffset === null) {
            // Try offset 12 (compact layout) — read first char
            const c12 = strPtr.add(12).readU16();
            // Verify: valid ASCII/printable char?
            if (c12 >= 0x20 && c12 < 0x7F) {
                _monoStringCharsOffset = 12;
            } else {
                // Try offset 16
                const c16 = strPtr.add(16).readU16();
                if (c16 >= 0x20 && c16 < 0x7F) {
                    _monoStringCharsOffset = 16;
                } else {
                    // Can't determine — fall back to slow path permanently
                    return readMonoString(strPtr);
                }
            }
            send({type:'info', msg:'QW10 mono string chars offset detected: ' + _monoStringCharsOffset});
        }
        const chars = strPtr.add(_monoStringCharsOffset).readUtf16String(len);
        return chars;
    } catch (e) {
        return readMonoString(strPtr);
    }
}

// QW4: Cache isCommandClassName results (5+ string ops + loop per call)
const _isCommandClassNameCache = new Map();
function isCommandClassName(className) {
    if (!className) return false;
    const cached = _isCommandClassNameCache.get(className);
    if (cached !== undefined) return cached;
    const simple = className.split('.').pop();
    let result = false;
    if (COMMAND_KIND[simple]) {
        result = true;
    } else {
        for (const key of Object.keys(COMMAND_KIND)) {
            if (simple === key || simple.startsWith(key + '`') || simple.endsWith(key) || simple.includes(key)) {
                result = true;
                break;
            }
        }
    }
    _isCommandClassNameCache.set(className, result);
    return result;
}

function resolveCommandKindInfo(className) {
    if (!className) return null;
    const simple = className.split('.').pop();
    if (COMMAND_KIND[simple]) {
        return { simpleName: simple, commandKey: simple, eventType: COMMAND_KIND[simple] };
    }
    for (const key of Object.keys(COMMAND_KIND)) {
        if (simple === key) {
            return { simpleName: simple, commandKey: key, eventType: COMMAND_KIND[key] };
        }
        if (simple.startsWith(key + '`')) {
            return { simpleName: simple, commandKey: key, eventType: COMMAND_KIND[key] };
        }
        if (simple.endsWith(key)) {
            return { simpleName: simple, commandKey: key, eventType: COMMAND_KIND[key] };
        }
        if (simple.includes(key)) {
            return { simpleName: simple, commandKey: key, eventType: COMMAND_KIND[key] };
        }
    }
    return null;
}

function isCommandParamType(typeName) {
    if (!typeName) return false;
    if (isCommandClassName(typeName)) return true;
    if (typeName.includes('INetCommand')) return true;
    if (typeName.includes('.ICommand')) return true;
    if (typeName.endsWith('.ICommand')) return true;
    if (typeName.includes('Command')) return true;
    return false;
}

function readGuid(base, off) {
    try {
        const b = base.add(off).readByteArray(16);
        if (!b) return null;
        const a = new Uint8Array(b);
        const h = (x) => ('0'+x.toString(16)).slice(-2);
        return h(a[3])+h(a[2])+h(a[1])+h(a[0])+'-'+h(a[5])+h(a[4])+'-'+h(a[7])+h(a[6])+'-'+h(a[8])+h(a[9])+'-'+h(a[10])+h(a[11])+h(a[12])+h(a[13])+h(a[14])+h(a[15]);
    } catch(e) { return null; }
}

function findDynamicField(objPtr, names) {
    try {
        if (!objPtr || objPtr.isNull()) return null;
        const klass = mono_object_get_class(objPtr);
        if (!klass || klass.isNull()) return null;
        const fields = getDynamicFieldsForKlass(klass);
        for (const name of names) {
            const field = fields.find(f => f && f.name === name);
            if (field) return field;
        }
    } catch (e) {}
    return null;
}

function readDynamicObjectField(objPtr, names) {
    const field = findDynamicField(objPtr, names);
    if (!field) return null;
    return readObjectFieldByInfo(objPtr, field);
}

function readDynamicI32Field(objPtr, names) {
    try {
        const field = findDynamicField(objPtr, names);
        if (!field) return null;
        return objPtr.add(field.offset).readS32();
    } catch (e) {
        return null;
    }
}

function readDynamicU32Field(objPtr, names) {
    try {
        const field = findDynamicField(objPtr, names);
        if (!field) return null;
        return objPtr.add(field.offset).readU32();
    } catch (e) {
        return null;
    }
}

function readDynamicU16Field(objPtr, names) {
    try {
        const field = findDynamicField(objPtr, names);
        if (!field) return null;
        return objPtr.add(field.offset).readU16();
    } catch (e) {
        return null;
    }
}

function readDynamicBoolField(objPtr, names) {
    try {
        const field = findDynamicField(objPtr, names);
        if (!field) return null;
        return objPtr.add(field.offset).readU8() !== 0;
    } catch (e) {
        return null;
    }
}

function readDynamicGuidField(objPtr, names) {
    try {
        const field = findDynamicField(objPtr, names);
        if (!field) return null;
        return readGuid(objPtr, field.offset);
    } catch (e) {
        return null;
    }
}

function readDynamicStringField(objPtr, names) {
    const strPtr = readDynamicObjectField(objPtr, names);
    return strPtr && !strPtr.isNull() ? readMonoString(strPtr) : null;
}

function readDynamicNullableU32Field(objPtr, names) {
    try {
        const field = findDynamicField(objPtr, names);
        if (!field) return null;
        const base = objPtr.add(field.offset);
        return base.readU8() ? base.add(4).readU32() : null;
    } catch (e) {
        return null;
    }
}

function readManagedIntArray(arrayPtr, limit) {
    if (!arrayPtr || arrayPtr.isNull()) return [];
    try {
        const length = getManagedArrayLength(arrayPtr);
        if (length <= 0) return [];
        const count = Math.min(length, limit || length, 32);
        const base = getManagedArrayDataPtr(arrayPtr);
        if (!isReadablePointer(base)) return [];
        const values = [];
        for (let i = 0; i < count; i++) {
            const addr = base.add(i * 4);
            if (!isReadableAddress(addr, 4)) break;
            values.push(addr.readS32());
        }
        return values;
    } catch (e) {
        return [];
    }
}

function readDynamicIntListField(objPtr, names) {
    try {
        const listPtr = readDynamicObjectField(objPtr, names);
        if (!listPtr || listPtr.isNull()) return [];
        const klass = mono_object_get_class(listPtr);
        if (!klass || klass.isNull()) return [];
        const fields = getDynamicFieldsForKlass(klass);
        const itemsField = findNamedField(fields, ['_items', 'items']);
        if (itemsField) {
            const itemsPtr = readObjectFieldByInfo(listPtr, itemsField);
            if (!itemsPtr || itemsPtr.isNull()) return [];
            const sizeField = findNamedField(fields, ['_size', 'size', '_count', 'count']);
            const size = sizeField ? readScalarField(listPtr, sizeField) : getManagedArrayLength(itemsPtr);
            return readManagedIntArray(itemsPtr, size);
        }
        return readManagedIntArray(listPtr, getManagedArrayLength(listPtr));
    } catch (e) {
        return [];
    }
}

function readManagedObjectPtrArray(arrayPtr, limit) {
    if (!arrayPtr || arrayPtr.isNull()) return [];
    try {
        const length = getManagedArrayLength(arrayPtr);
        if (length <= 0) return [];
        const count = Math.min(length, limit || length, 128);
        const base = getManagedArrayDataPtr(arrayPtr);
        if (!isReadablePointer(base)) return [];
        const values = [];
        for (let i = 0; i < count; i++) {
            const addr = base.add(i * Process.pointerSize);
            if (!isReadableAddress(addr, Process.pointerSize)) break;
            const objPtr = addr.readPointer();
            if (objPtr && !objPtr.isNull()) values.push(objPtr);
        }
        return values;
    } catch (e) {
        return [];
    }
}

function readManagedObjectList(listPtr, limit) {
    try {
        if (!listPtr || listPtr.isNull()) return [];
        const klass = mono_object_get_class(listPtr);
        if (!klass || klass.isNull()) return [];
        const fields = getDynamicFieldsForKlass(klass);
        const itemsField = findNamedField(fields, ['_items', 'items']);
        if (itemsField) {
            const itemsPtr = readObjectFieldByInfo(listPtr, itemsField);
            if (!itemsPtr || itemsPtr.isNull()) return [];
            const sizeField = findNamedField(fields, ['_size', 'size', '_count', 'count']);
            const size = sizeField ? Math.max(0, readScalarField(listPtr, sizeField) || 0) : getManagedArrayLength(itemsPtr);
            return readManagedObjectPtrArray(itemsPtr, size);
        }
        return readManagedObjectPtrArray(listPtr, getManagedArrayLength(listPtr));
    } catch (e) {
        return [];
    }
}

function readGameSimTemplateEventsFromList(eventsPtr) {
    try {
        if (!eventsPtr || eventsPtr.isNull()) return [];
        const eventPtrs = readManagedObjectList(eventsPtr, 96);
        if (!eventPtrs.length) return [];
        const templateEvents = [];
        for (const eventPtr of eventPtrs) {
            if (!eventPtr || eventPtr.isNull()) continue;
            let className = null;
            try {
                const klass = mono_object_get_class(eventPtr);
                className = klass && !klass.isNull() ? classFullName(klass) : null;
            } catch (e) {
                className = null;
            }
            if (!className) continue;

            let eventType = null;
            if (className.includes('GameSimEventCardDealt')) eventType = 'card_dealt';
            else if (className.includes('GameSimEventCardSpawned')) eventType = 'card_spawned';
            if (!eventType) continue;

            const instanceId = readDynamicStringField(eventPtr, ['InstanceId', '<InstanceId>k__BackingField']);
            const templateId = readDynamicStringField(eventPtr, ['TemplateId', '<TemplateId>k__BackingField']);
            if (!instanceId || !templateId) continue;

            const typeInt = readDynamicI32Field(eventPtr, ['Type', '<Type>k__BackingField']);
            const cardType = typeInt !== null ? (E_CARD_TYPE[typeInt] || typeInt) : inferCardTypeFromInstanceId(instanceId);

            templateEvents.push({
                event_type: eventType,
                class_name: className,
                instance_id: instanceId,
                template_id: templateId,
                card_type: cardType,
            });
        }
        return templateEvents;
    } catch (e) {
        send({type:'debug',msg:'readGameSimTemplateEventsFromList:'+e});
        return [];
    }
}

function readMessageIdFromNetMessage(objPtr, classKey) {
    const msgPtr = readObjectField(objPtr, classKey, ['MessageId', '<MessageId>k__BackingField']);
    return msgPtr && !msgPtr.isNull() ? readMonoString(msgPtr) : null;
}

// QW10: Fast message ID reader — uses fieldInfoCache offsets + direct pointer read
// instead of readObjectField (which calls mono_field_get_value via NativeFunction).
// Saves ~2-3 NativeFunction calls per hook.
function _fastReadMessageId(objPtr, classKey) {
    try {
        const info = fieldInfoCache[classKey];
        if (!info) return readMessageIdFromNetMessage(objPtr, classKey); // fallback
        const field = info['MessageId'] || info['<MessageId>k__BackingField'];
        if (!field) return readMessageIdFromNetMessage(objPtr, classKey); // fallback
        const msgPtr = objPtr.add(field.offset).readPointer();
        if (!msgPtr || msgPtr.isNull()) return null;
        return _directReadMonoString(msgPtr);
    } catch (e) { return null; }
}

// QW10: Fast Data field reader — uses fieldInfoCache offsets + direct pointer read.
function _fastReadDataField(objPtr, classKey) {
    try {
        const info = fieldInfoCache[classKey];
        if (!info) return readObjectField(objPtr, classKey, ['Data', '<Data>k__BackingField']); // fallback
        const field = info['Data'] || info['<Data>k__BackingField'];
        if (!field) return readObjectField(objPtr, classKey, ['Data', '<Data>k__BackingField']); // fallback
        const dataPtr = objPtr.add(field.offset).readPointer();
        return (dataPtr && !dataPtr.isNull()) ? dataPtr : null;
    } catch (e) { return null; }
}

// Discovery
const searchTargets = [
    {ns:'TheBazaar',cls:'GameStateHandler'},{ns:'',cls:'GameStateHandler'},{ns:'TheBazaar.Runtime',cls:'GameStateHandler'},
    {ns:'BazaarGameShared.Infra.Messages',cls:'NetMessageGameStateSync'},
    {ns:'BazaarGameShared.Infra.Messages',cls:'NetMessageGameSim'},
    {ns:'BazaarGameShared.Infra.Messages',cls:'NetMessageCombatSim'},
    {ns:'BazaarGameShared.Infra.Messages',cls:'NetMessageRunInitialized'},
    {ns:'BazaarGameShared.Infra.Messages',cls:'GameStateSnapshotDTO'},
    {ns:'BazaarGameShared.Infra.Messages',cls:'RunSnapshotDTO'},
    {ns:'BazaarGameShared.Infra.Messages',cls:'RunStateSnapshotDTO'},
    {ns:'BazaarGameShared.Infra.Messages',cls:'PlayerSnapshotDTO'},
    {ns:'BazaarGameShared.Infra.Messages',cls:'CardSnapshotDTO'},
];

const foundClasses = {}, fieldCache = {}, fieldInfoCache = {}, fieldNullLogCounts = {}, dynamicFieldInfoCache = {}, graphSummaryLogCounts = {}, collectionLayoutLogCounts = {}, cardBucketLogCounts = {}, cardCollectionLogCounts = {};
for (const t of searchTargets) {
    const klass = findClass(t.ns, t.cls);
    if (klass) {
        foundClasses[t.cls] = {klass, ns:t.ns};
        if (t.cls === 'GameStateHandler') {
            const methods = getMethods(klass);
            send({type:'info',msg:'GameStateHandler methods ('+methods.length+'):'});
            for (const m of methods) send({type:'debug',msg:'  '+formatMethod(m)});
            const handlerFields = getFields(klass);
            send({type:'info',msg:'GameStateHandler fields ('+handlerFields.length+'):'});
            for (const f of handlerFields) {
                send({type:'debug',msg:'  '+f.name+'@'+f.offset+(f.type ? ' : '+f.type : '')});
            }
        }
        const fields = getFields(klass);
        const map = {};
        const info = {};
        for (const f of fields) {
            map[f.name] = f.offset;
            info[f.name] = f;
        }
        fieldCache[t.cls] = map;
        fieldInfoCache[t.cls] = info;
        if (t.cls.endsWith('DTO')||t.cls.endsWith('Sync')||t.cls.endsWith('Sim')||t.cls.endsWith('Initialized'))
            send({type:'debug',msg:t.cls+' fields: '+fields.map(f=>f.name+'@'+f.offset).join(', ')});
    }
}

// DTO readers
function readRunSnapshot(p){const o=fieldCache['RunSnapshotDTO'];if(!o||!p||p.isNull())return{};const r={};try{if('GameModeId'in o)r.game_mode_id=readGuid(p,o['GameModeId']);if('Day'in o)r.day=p.add(o['Day']).readU32();if('Hour'in o)r.hour=p.add(o['Hour']).readU32();if('Victories'in o)r.victories=p.add(o['Victories']).readU32();if('Defeats'in o)r.defeats=p.add(o['Defeats']).readU32();if('HasVisitedFates'in o)r.visited_fates=p.add(o['HasVisitedFates']).readU8()!==0;if('DataVersion'in o)r.data_version=readMonoString(p.add(o['DataVersion']).readPointer());}catch(e){send({type:'debug',msg:'readRun:'+e});}return r;}

function readRunStateSnapshot(p){const o=fieldCache['RunStateSnapshotDTO'];if(!o||!p||p.isNull())return{};const r={};try{if('StateName'in o){const v=p.add(o['StateName']).readS32();r.state=E_RUN_STATE[v]||('Unknown('+v+')');r.state_int=v;}
// Re-enabled from W/L-only scope-down
if('CurrentEncounterId'in o)r.current_encounter_id=readMonoString(p.add(o['CurrentEncounterId']).readPointer());
if('RerollCost'in o){try{const b=p.add(o['RerollCost']);if(b.readU8())r.reroll_cost=b.add(4).readU32();}catch(e){}}
if('RerollsRemaining'in o){try{const b=p.add(o['RerollsRemaining']);if(b.readU8())r.rerolls_remaining=b.add(4).readU32();}catch(e){}}
// F4: selection_set gated to interesting states (Choice/Loot/LevelUp/Pedestal/Encounter)
if('SelectionSet'in o&&ACTION_CARD_STATES[r.state])r.selection_set=readStringList(p.add(o['SelectionSet']).readPointer());
}catch(e){send({type:'debug',msg:'readState:'+e});}return r;}

function readPlayerSnapshot(p){const o=fieldCache['PlayerSnapshotDTO'];if(!o||!p||p.isNull())return{};const r={};try{if('Hero'in o){const v=p.add(o['Hero']).readS32();r.hero=E_HERO[v]||('Unknown('+v+')');}
// Re-enabled from W/L-only scope-down (F10)
if('UnlockedSlots'in o)r.unlocked_slots=p.add(o['UnlockedSlots']).readU16();
// Keep only the live HUD / enrichment attributes to reduce snapshot work.
if('Attributes'in o){const dp=readObjectField(p,'PlayerSnapshotDTO',['Attributes']);if(dp&&!dp.isNull()){const attrs=readEnumIntDict(dp,'PlayerSnapshotDTO.Attributes',KEEP_PLAYER_ATTR_IDS,KEEP_PLAYER_ATTR_COUNT);for(const[k,v]of Object.entries(attrs))r[E_PLAYER_ATTRIBUTE[parseInt(k)]||('attr_'+k)]=v;}else send({type:'debug',msg:'PlayerSnapshotDTO.Attributes pointer was null'});}}catch(e){send({type:'debug',msg:'readPlayer:'+e});}return r;}

// Re-enabled from W/L-only scope-down (F1)
function readCardSnapshot(p){const o=fieldCache['CardSnapshotDTO'];if(!o||!isReadablePointer(p))return{};const c={};try{if('InstanceId'in o)c.instance_id=readMonoString(safeReadPointer(p,o['InstanceId']));if('TemplateId'in o)c.template_id=readGuid(p,o['TemplateId']);if('Tier'in o){const v=p.add(o['Tier']).readS32();c.tier=E_TIER[v]||v;}if('Type'in o){const v=p.add(o['Type']).readS32();c.type=E_CARD_TYPE[v]||v;}if('Size'in o){const v=p.add(o['Size']).readS32();c.size=E_CARD_SIZE[v]||v;}if('Owner'in o){try{const b=p.add(o['Owner']);if(b.readU8()){const v=b.add(4).readS32();c.owner=E_COMBATANT[v]||v;}else c.owner=null;}catch(e){c.owner=null;}}if('Socket'in o){try{const b=p.add(o['Socket']);c.socket=b.readU8()?b.add(4).readS32():null;}catch(e){c.socket=null;}}if('Section'in o){try{const b=p.add(o['Section']);if(b.readU8()){const v=b.add(4).readS32();c.section=E_INVENTORY_SECTION[v]||v;}else c.section=null;}catch(e){c.section=null;}}if(isSuspiciousTemplateId(c.template_id)){const probe=buildCardDebugProbe(p,{info:fieldInfoCache['CardSnapshotDTO']},false);if(probe)c._debug_probe=probe;c._debug_source='CardSnapshotDTO';}}catch(e){send({type:'debug',msg:'readCard:'+e});}return c;}

// QW1: _fieldInfoPrewarmed â€” set to true after attach-time pre-warming; once set,
// getFieldInfoForTypeName skips cold class walks on the hook thread.
let _fieldInfoPrewarmed = false;

function getFieldInfoForTypeName(typeName){
    try{
        const parsed=parseTypeName(typeName);
        if(!parsed) return null;
        const fullName=(parsed.ns?parsed.ns+'.':'')+parsed.cls;
        if(fieldInfoCache[fullName]) return {map:fieldCache[fullName], info:fieldInfoCache[fullName], fullName:fullName};
        // QW1: after pre-warming, skip cold class walks on the hook thread to eliminate 50-100ms spikes
        if(_fieldInfoPrewarmed){
            send({type:'debug',msg:'skipping cold class: '+fullName});
            return null;
        }
        const klass=findClass(parsed.ns, parsed.cls);
        if(!klass || klass.isNull()) return null;
        const fields=getFields(klass);
        const map={};
        const info={};
        for(const f of fields){
            map[f.name]=f.offset;
            info[f.name]=f;
        }
        fieldCache[fullName]=map;
        fieldInfoCache[fullName]=info;
        logCardCollectionInfo('card-type:'+fullName,'Resolved card value type '+fullName+' fields: '+fields.map(f=>f.name+'@'+f.offset+(f.type?':'+f.type:'')).join(', '));
        return {map:map, info:info, fullName:fullName};
    }catch(e){
        return null;
    }
}

function isInlineCardValueType(typeName){
    const t=String(typeName||'');
    if(!t) return false;
    if(t.startsWith('valuetype ')) return true;
    if(t.includes('SimUpdateCard')) return true;
    return false;
}

function normalizeValueTypeOffset(offset){
    const headerSize = Process.pointerSize * 2;
    return offset >= headerSize ? (offset - headerSize) : offset;
}

function readEntryStringKey(entryBase, field){
    if(!field) return null;
    const offsets = [field.offset];
    const norm = normalizeValueTypeOffset(field.offset);
    if(norm !== field.offset) offsets.push(norm);
    for(const off of offsets){
        const ptrValue = safeReadPointer(entryBase, off);
        if(ptrValue && !ptrValue.isNull()){
            const s = readMonoString(ptrValue);
            if(s) return s;
        }
    }
    return null;
}

function getCandidateFieldOffsets(field){
    if(!field) return [];
    const norm = normalizeValueTypeOffset(field.offset);
    const offsets = [norm];
    if(norm !== field.offset) offsets.push(field.offset);
    return offsets;
}

function inferCardTypeFromInstanceId(instanceId){
    const value = String(instanceId || '');
    if(value.startsWith('skl_')) return 'Skill';
    if(value.startsWith('itm_')) return 'Item';
    if(value.startsWith('com_')) return 'Companion';
    if(value.startsWith('enc_') || value.startsWith('ste_') || value.startsWith('ped_')) return 'Encounter';
    return null;
}

function isSuspiciousTemplateId(templateId){
    const value = String(templateId || '').toLowerCase();
    if(!value) return false;
    if(value === '00000000-0000-0000-0000-000000000000') return true;
    return value.endsWith('-0000-0000-000000000000');
}

function shouldProbeCardField(field){
    if(!field) return false;
    const name = String(field.name || '').toLowerCase();
    const typeName = String(field.type || '');
    if(typeName.startsWith('System.String') || typeName.startsWith('System.Guid')) return true;
    return name.includes('template') || name.includes('instance') || name.endsWith('id') ||
           name.includes('name') || name.includes('slug') || name.includes('key') ||
           name.includes('type') || name.includes('card');
}

function isDebugProbePrimitiveType(typeName){
    typeName = String(typeName || '');
    return typeName.startsWith('System.String') ||
           typeName.startsWith('System.Guid') ||
           typeName.startsWith('System.Boolean') ||
           typeName.startsWith('System.Nullable<') ||
           typeName.startsWith('System.Byte') ||
           typeName.startsWith('System.SByte') ||
           typeName.startsWith('System.UInt16') ||
           typeName.startsWith('System.Int16') ||
           typeName.startsWith('System.UInt32') ||
           typeName.startsWith('System.Int32');
}

function shouldRecurseCardField(field){
    if(!field) return false;
    const name = String(field.name || '').toLowerCase();
    const typeName = String(field.type || '');
    if(!typeName || typeName.startsWith('System.')) return false;
    return name.includes('template') ||
           name.includes('instance') ||
           name.includes('name') ||
           name.includes('display') ||
           name.includes('definition') ||
           name.includes('skill') ||
           name.includes('card') ||
           name.includes('meta') ||
           name.includes('data');
}

function readDebugProbeField(basePtr, field, inlineValue){
    if(!field) return null;
    const offsets = getCandidateFieldOffsets(field);
    for(const rawOff of offsets){
        const off = inlineValue ? rawOff : field.offset;
        try{
            const typeName = String(field.type || '');
            if(typeName.startsWith('System.String')){
                const ptrValue = safeReadPointer(basePtr, off);
                if(ptrValue && !ptrValue.isNull()){
                    const strValue = readMonoString(ptrValue);
                    if(strValue) return strValue;
                }
                continue;
            }
            if(typeName.startsWith('System.Guid')){
                const guidValue = readGuid(basePtr, off);
                if(guidValue) return guidValue;
                continue;
            }
            if(typeName.startsWith('System.Boolean')){
                return !!basePtr.add(off).readU8();
            }
            if(typeName.startsWith('System.Nullable<')){
                const nullableValue = readMaybeNullableI32(basePtr, Object.assign({}, field, {offset: off}), inlineValue);
                if(nullableValue !== null) return nullableValue;
                continue;
            }
            if(typeName.startsWith('System.Byte')) return basePtr.add(off).readU8();
            if(typeName.startsWith('System.SByte')) return basePtr.add(off).readS8();
            if(typeName.startsWith('System.UInt16')) return basePtr.add(off).readU16();
            if(typeName.startsWith('System.Int16')) return basePtr.add(off).readS16();
            if(typeName.startsWith('System.UInt32')) return basePtr.add(off).readU32();
            if(typeName.startsWith('System.Int32')) return basePtr.add(off).readS32();
        }catch(e){}
    }
    return null;
}

function buildCardDebugProbe(basePtr, fieldMeta, inlineValue, depth, maxCount){
    if(!fieldMeta || !fieldMeta.info) return null;
    depth = depth || 0;
    maxCount = maxCount || 20;
    const probe = {};
    let count = 0;
    const nestedAttempts = [];
    const nestedTypes = [];
    const fields = Object.values(fieldMeta.info);
    for(const field of fields){
        if(!shouldProbeCardField(field)) continue;
        const value = readDebugProbeField(basePtr, field, inlineValue);
        if(value === null || value === undefined || value === '') continue;
        probe[field.name] = value;
        count++;
        if(count >= maxCount) break;
    }
    if(depth < 1 && count < maxCount){
        for(const field of fields){
            if(count >= maxCount) break;
            if(!shouldRecurseCardField(field)) continue;
            const offsets = getCandidateFieldOffsets(field);
            let fieldStatus = 'no_readable_pointer';
            for(const rawOff of offsets){
                if(count >= maxCount) break;
                const off = inlineValue ? rawOff : field.offset;
                try{
                    const nestedPtr = safeReadPointer(basePtr, off);
                    if(!nestedPtr || nestedPtr.isNull()){
                        fieldStatus = 'null_pointer';
                        continue;
                    }
                    const klass = mono_object_get_class(nestedPtr);
                    const className = klass && !klass.isNull() ? classFullName(klass) : null;
                    if(className) nestedTypes.push(field.name + ':' + className);
                    const nestedMeta = (className ? getFieldInfoForTypeName(className) : null) || getFieldInfoForTypeName(field.type);
                    if(!nestedMeta || !nestedMeta.info){
                        fieldStatus = className ? ('missing_meta:' + className) : 'missing_meta';
                        continue;
                    }
                    const nestedProbe = buildCardDebugProbe(nestedPtr, nestedMeta, false, depth + 1, maxCount - count);
                    if(!nestedProbe){
                        fieldStatus = className ? ('empty_probe:' + className) : 'empty_probe';
                        continue;
                    }
                    fieldStatus = className ? ('read:' + className) : 'read';
                    for(const [nestedKey, nestedValue] of Object.entries(nestedProbe)){
                        if(count >= maxCount) break;
                        const flatKey = field.name + '.' + nestedKey;
                        if(flatKey in probe) continue;
                        probe[flatKey] = nestedValue;
                        count++;
                    }
                    if(className && count < maxCount){
                        probe[field.name + '.__type'] = className;
                        count++;
                    }
                    break;
                }catch(e){}
            }
            if(nestedAttempts.length < 10){
                nestedAttempts.push(field.name + ':' + String(field.type || '') + ':' + fieldStatus);
            }
        }
    }
    const onlyPrimitiveIds =
        count > 0 &&
        Object.keys(probe).every(k => k === 'InstanceId' || k === 'TemplateId');
    if(onlyPrimitiveIds){
        if(nestedAttempts.length) probe.__nested_attempts = nestedAttempts;
        if(nestedTypes.length) probe.__nested_types = Array.from(new Set(nestedTypes)).slice(0, 8);
    }
    return count > 0 ? probe : null;
}

function readMaybeNullableI32(basePtr, field, inlineValue){
    if(!field) return null;
    const offsets = getCandidateFieldOffsets(field);
    for(const rawOff of offsets){
        const off = inlineValue ? rawOff : field.offset;
        try{
            if((field.type || '').startsWith('System.Nullable<')){
                const b = basePtr.add(off);
                if(b.readU8()) return b.add(4).readS32();
                continue;
            }
            return basePtr.add(off).readS32();
        }catch(e){}
    }
    return null;
}

function readPlacementField(basePtr, placementField, names){
    const placementMeta = placementField ? getFieldInfoForTypeName(placementField.type) : null;
    if(!placementMeta || !placementMeta.info) return null;

    for(const off of getCandidateFieldOffsets(placementField)){
        // Try object-reference layout first.
        const placementPtr = safeReadPointer(basePtr, off);
        if(placementPtr && !placementPtr.isNull()){
            try{
                const klass = mono_object_get_class(placementPtr);
                const className = klass && !klass.isNull() ? classFullName(klass) : null;
                if(!placementMeta.fullName || !className || className === placementMeta.fullName){
                    for(const name of names){
                        const nested = placementMeta.info[name];
                        if(!nested) continue;
                        const value = readMaybeNullableI32(placementPtr, nested, false);
                        if(value !== null && value !== undefined) return value;
                    }
                }
            }catch(e){}
        }

        // Fall back to inline valuetype layout.
        const placementBase = basePtr.add(off);
        for(const name of names){
            const nested = placementMeta.info[name];
            if(!nested) continue;
            const value = readMaybeNullableI32(placementBase, nested, true);
            if(value !== null && value !== undefined) return value;
        }
    }

    return null;
}

// Re-enabled from W/L-only scope-down (F1)
function readCardFromFieldMap(basePtr, fieldMeta, inlineValue){
    if(!fieldMeta || !fieldMeta.map || !basePtr) return {};
    const fieldMap = fieldMeta.map;
    const fieldInfo = fieldMeta.info || {};
    const fieldOffset = (name) => {
        const raw = fieldMap[name];
        return inlineValue ? normalizeValueTypeOffset(raw) : raw;
    };
    const c={};
    try{
        if('InstanceId' in fieldMap){
            const strPtr=safeReadPointer(basePtr, fieldOffset('InstanceId'));
            if(strPtr && !strPtr.isNull()) c.instance_id=readMonoString(strPtr);
        }
        if('TemplateId' in fieldMap) c.template_id=readGuid(basePtr, fieldOffset('TemplateId'));
        if('Tier' in fieldMap){
            const v=readMaybeNullableI32(basePtr, fieldInfo['Tier'] || {offset: fieldOffset('Tier'), type:''}, inlineValue);
            if(v !== null) c.tier=E_TIER[v]||v;
        }
        if('Type' in fieldMap){
            const v=readMaybeNullableI32(basePtr, fieldInfo['Type'] || {offset: fieldOffset('Type'), type:''}, inlineValue);
            if(v !== null) c.type=E_CARD_TYPE[v]||v;
        }
        if('Size' in fieldMap){
            const v=readMaybeNullableI32(basePtr, fieldInfo['Size'] || {offset: fieldOffset('Size'), type:''}, inlineValue);
            if(v !== null) c.size=E_CARD_SIZE[v]||v;
        }
        if('Owner' in fieldMap){
            try{
                const b=basePtr.add(fieldOffset('Owner'));
                if(b.readU8()){
                    const v=b.add(4).readS32();
                    c.owner=E_COMBATANT[v]||v;
                }else c.owner=null;
            }catch(e){ c.owner=null; }
        }
        if('Socket' in fieldMap){
            try{
                const b=basePtr.add(fieldOffset('Socket'));
                c.socket=b.readU8()?b.add(4).readS32():null;
            }catch(e){ c.socket=null; }
        }
        if('Section' in fieldMap){
            try{
                const b=basePtr.add(fieldOffset('Section'));
                if(b.readU8()){
                    const v=b.add(4).readS32();
                    c.section=E_INVENTORY_SECTION[v]||v;
                }else c.section=null;
            }catch(e){ c.section=null; }
        }
        if((c.owner === undefined || c.owner === null) && fieldInfo['Placement']){
            const ownerVal = readPlacementField(basePtr, fieldInfo['Placement'], ['Owner', '<Owner>k__BackingField', 'CardOwner', 'Combatant']);
            if(ownerVal !== null) c.owner = E_COMBATANT[ownerVal] || ownerVal;
        }
        if((c.socket === undefined || c.socket === null) && fieldInfo['Placement']){
            const socketVal = readPlacementField(basePtr, fieldInfo['Placement'], ['Socket', '<Socket>k__BackingField', 'SocketId', 'BoardSlot', 'Slot', 'Position', 'Index']);
            if(socketVal !== null) c.socket = socketVal;
        }
        if((c.section === undefined || c.section === null) && fieldInfo['Placement']){
            const sectionVal = readPlacementField(basePtr, fieldInfo['Placement'], ['Section', '<Section>k__BackingField', 'InventorySection']);
            if(sectionVal !== null) c.section = E_INVENTORY_SECTION[sectionVal] || sectionVal;
        }
        if((c.type === undefined || c.type === null) && c.instance_id){
            c.type = inferCardTypeFromInstanceId(c.instance_id);
        }
        if(isSuspiciousTemplateId(c.template_id)){
            const probe = buildCardDebugProbe(basePtr, fieldMeta, inlineValue);
            if(probe) c._debug_probe = probe;
            c._debug_source = fieldMeta.fullName || 'field_map';
        }
    }catch(e){
        send({type:'debug',msg:'readCardFromFieldMap:'+e});
    }
    return c;
}

// Re-enabled from W/L-only scope-down (F1)
function cardHasUsefulData(card){ return !!(card && (card.instance_id || card.template_id)); }

// Re-enabled from W/L-only scope-down (F1): calls readCardFromFieldMap with value slot's field metadata
function readCardFromValueSlot(entryBase, valueField, valueFieldInfo, inlineValue){
    if(!valueField || !valueFieldInfo || !valueFieldInfo.map) return null;
    const offsets = getCandidateFieldOffsets(valueField);
    const expectedClass = valueFieldInfo.fullName || null;

    // First, try treating the slot as an object reference. The latest entry probe
    // suggests SimUpdateCard may be stored as a reference in Dictionary.Entry.
    for(const off of offsets){
        const ptrValue = safeReadPointer(entryBase, off);
        if(!ptrValue || ptrValue.isNull()) continue;
        try{
            const klass = mono_object_get_class(ptrValue);
            const className = klass && !klass.isNull() ? classFullName(klass) : null;
            if(expectedClass && className && className !== expectedClass) continue;
            const card = readCardFromFieldMap(ptrValue, valueFieldInfo, false);
            if(cardHasUsefulData(card)) return card;
        }catch(e){}
    }

    // Fall back to inline valuetype decoding if the slot isn't a reference.
    if(inlineValue){
        for(const off of offsets){
            const card = readCardFromFieldMap(entryBase.add(off), valueFieldInfo, true);
            if(cardHasUsefulData(card)) return card;
        }
    }

    return null;
}

function logCardBucketMiss(card){
    try{
        const key=[card.owner,card.section,card.socket,card.type].join('|');
        cardBucketLogCounts[key]=(cardBucketLogCounts[key]||0)+1;
        if(cardBucketLogCounts[key] <= 5){
            send({type:'debug',msg:'Uncategorized player card owner='+card.owner+' section='+card.section+' socket='+card.socket+' type='+card.type+' instance='+card.instance_id+' template='+card.template_id});
        }
    }catch(e){}
}

function normalizeCombatantOwner(owner){
    if(owner === undefined) return undefined;
    if(owner === null) return null;
    if(owner === 'Player' || owner === 'Opponent') return owner;
    if(owner === 0 || owner === '0') return 'Player';
    if(owner === 1 || owner === '1') return 'Opponent';
    return owner;
}

function logCardCollectionInfo(key, msg){
    try{
        cardCollectionLogCounts[key]=(cardCollectionLogCounts[key]||0)+1;
        if(cardCollectionLogCounts[key] <= 3){
            send({type:'info',msg:msg});
        }
    }catch(e){}
}

function logCardEntryProbe(key, msg){
    try{
        cardCollectionLogCounts[key]=(cardCollectionLogCounts[key]||0)+1;
        if(cardCollectionLogCounts[key] <= 1){
            send({type:'info',msg:msg});
        }
    }catch(e){}
}

function looksLikeCardCollectionField(field){
    if(!field) return false;
    const name=(field.name||'').toLowerCase();
    const type=field.type||'';
    if(name === 'cards' || name.endsWith('cards')) return true;
    if(name.includes('board') || name.includes('stash') || name.includes('skill')) return true;
    return type.includes('CardSnapshotDTO');
}

function findCardCollectionField(objPtr, preferredNames){
    try{
        if(!objPtr || objPtr.isNull()) return null;
        const klass=mono_object_get_class(objPtr);
        if(!klass || klass.isNull()) return null;
        const className=classFullName(klass) || '?';
        const fields=getDynamicFieldsForKlass(klass);
        for(const name of preferredNames || []){
            const field=fields.find(f=>f && f.name === name);
            if(!field) continue;
            const ptrValue=readObjectFieldByInfo(objPtr, field);
            if(ptrValue && !ptrValue.isNull()) return {ptr:ptrValue, field:field, className:className, fields:fields};
        }
        for(const field of fields){
            if(!looksLikeCardCollectionField(field)) continue;
            const ptrValue=readObjectFieldByInfo(objPtr, field);
            if(ptrValue && !ptrValue.isNull()) return {ptr:ptrValue, field:field, className:className, fields:fields};
        }
        logCardCollectionInfo('missing:'+className,'No readable card collection field on '+className+'; fields: '+describeFieldLayout(fields));
    }catch(e){}
    return null;
}

function bucketCard(card, snapshot){
    if(!card || !snapshot)return;
    const owner = normalizeCombatantOwner(card.owner);
    card.owner = owner;

    if(owner === 'Opponent'){
        if(CAPTURE_OPPONENT_BOARD){
            snapshot.opponent_board.push(card);
        }
        return;
    }
    if(owner === null || owner === undefined){
        if(card.type === 'Skill'){
            snapshot.player_skills.push(card);
            return;
        }
        if(card.section === 'Stash' && card.type !== 'Encounter'){
            snapshot.player_stash.push(card);
            return;
        }
        if(card.section === 'Hand'){
            snapshot.player_board.push(card);
            return;
        }
        if(card.type !== 'Encounter'){
            snapshot.offered.push(card);
            return;
        }
        snapshot.offered.push(card);
        return;
    }
    if(owner !== 'Player'){
        logCardBucketMiss(card);
        return;
    }
    if(card.type === 'Skill'){
        snapshot.player_skills.push(card);
        return;
    }
    if(card.section === 'Stash'){
        snapshot.player_stash.push(card);
        return;
    }
    if(card.section === 'Hand' || (card.socket !== null && card.socket !== undefined)){
        snapshot.player_board.push(card);
        return;
    }
    logCardBucketMiss(card);
}

function readStringList(lp){if(!lp||lp.isNull())return[];try{const sz=getManagedArrayLength(lp);if(sz<=0||sz>1000)return[];const base=getManagedArrayDataPtr(lp);const r=[];for(let i=0;i<sz;i++){const ep=base.add(i*Process.pointerSize).readPointer();if(ep&&!ep.isNull())r.push(readMonoString(ep));}return r;}catch(e){return[];}}

function describeFieldLayout(fields){return fields.map(f=>f.name+'@'+f.offset+(f.type?':'+f.type:'')).join(', ');}

function logCollectionLayoutOnce(prefix, klass, fields){try{const className=classFullName(klass)||prefix;const key=prefix+':'+className;collectionLayoutLogCounts[key]=(collectionLayoutLogCounts[key]||0)+1;if(collectionLayoutLogCounts[key]>1)return;send({type:'debug',msg:prefix+' '+className+' fields: '+describeFieldLayout(fields)});}catch(e){}}

function logDictionaryLayoutOnce(prefix, dictKlass, dictFields, entryKlass, entryFields, meta, sampleEntries){try{const dictName=classFullName(dictKlass)||'?';const entryName=classFullName(entryKlass)||'?';const key=prefix+':'+dictName+':'+entryName;collectionLayoutLogCounts[key]=(collectionLayoutLogCounts[key]||0)+1;if(collectionLayoutLogCounts[key]>1)return;let msg=prefix+' dict='+dictName+' {'+describeFieldLayout(dictFields)+'}';msg+=' entry='+entryName+' {'+describeFieldLayout(entryFields)+'}';if(meta){const countText=meta.count===undefined?'?':meta.count;const arrLenText=meta.arrLen===undefined?'?':meta.arrLen;const entrySizeText=meta.entrySize===undefined?'?':meta.entrySize;msg+=' count='+countText+' arrLen='+arrLenText+' entrySize='+entrySizeText;}if(sampleEntries&&sampleEntries.length>0)msg+=' sample=['+sampleEntries.join(', ')+']';send({type:'debug',msg:msg});}catch(e){}}

function findNamedField(fields, names){for(const name of names){const field=fields.find(f=>f.name===name);if(field)return field;}return null;}

function readScalarField(base, field){if(!field)return null;const typeName=field.type||'';if(typeName.startsWith('System.Byte'))return base.add(field.offset).readU8();if(typeName.startsWith('System.SByte'))return base.add(field.offset).readS8();if(typeName.startsWith('System.UInt16'))return base.add(field.offset).readU16();if(typeName.startsWith('System.Int16'))return base.add(field.offset).readS16();if(typeName.startsWith('System.UInt32'))return base.add(field.offset).readU32();return base.add(field.offset).readS32();}

function getManagedArrayLength(arrayPtr){if(!isReadablePointer(arrayPtr))return 0;try{const lenAddr=arrayPtr.add(3*Process.pointerSize);if(!isReadableAddress(lenAddr,4))return 0;return lenAddr.readS32();}catch(e){return 0;}}

function getManagedArrayDataPtr(arrayPtr){if(!isReadablePointer(arrayPtr))return ptr(0);try{const dataPtr=arrayPtr.add(4*Process.pointerSize);return isReadablePointer(dataPtr)?dataPtr:ptr(0);}catch(e){return ptr(0);}}

// Re-enabled from W/L-only scope-down (F1)
function readCardDictionary(sp, fields){try{if(DISABLE_DICTIONARY_PROBING)return[];if(!mono_class_get_element_class||!mono_class_value_size)return[];let eO=-1,cO=-1;for(const f of fields){if(f.name==='_entries'||f.name==='entries')eO=f.offset;if(f.name==='_count'||f.name==='count')cO=f.offset;}if(eO<0||cO<0)return[];const ea=safeReadPointer(sp,eO);const count=sp.add(cO).readS32();if(!isReadablePointer(ea)||count<=0)return[];const arrayKlass=mono_object_get_class(ea);if(!arrayKlass||arrayKlass.isNull())return[];const entryKlass=mono_class_get_element_class(arrayKlass);if(!entryKlass||entryKlass.isNull())return[];const entryFields=getFields(entryKlass);const hashField=entryFields.find(f=>f.name==='hashCode'||f.name==='_hashCode');const keyField=entryFields.find(f=>f.name==='key'||f.name==='Key');const valueField=entryFields.find(f=>f.name==='value'||f.name==='Value'||(f.type&&(f.type.includes('CardSnapshotDTO')||f.type.includes('SimUpdateCard'))));if(!valueField)return[];const inlineValue=isInlineCardValueType(valueField.type);const valueFieldInfo=inlineValue?getFieldInfoForTypeName(valueField.type):null;const align=Memory.alloc(4);align.writeU32(0);const entrySize=mono_class_value_size(entryKlass,align);if(!entrySize||entrySize<=0)return[];const arrLen=getManagedArrayLength(ea);const base=getManagedArrayDataPtr(ea);if(!isReadablePointer(base)||arrLen<=0)return[];const cards=[];const limit=Math.min(arrLen,Math.max(count+16,count),500);for(let i=0;i<limit&&cards.length<count;i++){try{const eb=base.add(i*entrySize);let hashValue=null;if(hashField){for(const off of getCandidateFieldOffsets(hashField)){if(isReadableAddress(eb.add(off),4)){hashValue=eb.add(off).readS32();break;}}if(hashValue!==null&&hashValue<0)continue;}const entryKey=keyField?readEntryStringKey(eb,keyField):null;if(cards.length===0&&keyField&&valueField){logCardEntryProbe('entry-probe:'+(valueField.type||'?'),'Entry probe first-live key='+entryKey+' hash='+hashValue+' keyOffset='+keyField.offset+' valueOffset='+valueField.offset+' entrySize='+entrySize+' entryFields='+describeFieldLayout(entryFields));}let card=null;if(inlineValue&&valueFieldInfo&&valueFieldInfo.map){card=readCardFromValueSlot(eb,valueField,valueFieldInfo,true);}else{for(const off of getCandidateFieldOffsets(valueField)){const vp=safeReadPointer(eb,off);if(vp){card=readCardSnapshot(vp);if(cardHasUsefulData(card))break;}}}if(!card&&entryKey){card={instance_id:entryKey,type:inferCardTypeFromInstanceId(entryKey)};}if(card&&(!card.instance_id)&&entryKey){card.instance_id=entryKey;}if(card&&(!card.type)&&card.instance_id){card.type=inferCardTypeFromInstanceId(card.instance_id);}if(card&&card.instance_id)cards.push(card);}catch(e){}}if(cards.length===0&&count>0){const dictKlass=mono_object_get_class(sp);const valueType=valueField.type||'?';logCardCollectionInfo('dict-empty:'+(classFullName(dictKlass)||'?')+':'+valueType,'Card dictionary '+(classFullName(dictKlass)||'?')+' value='+valueType+' count='+count+' yielded 0 cards; fields: '+describeFieldLayout(fields));}return cards;}catch(e){send({type:'debug',msg:'readCardDictionary:'+e});return[];}}

// Re-enabled from W/L-only scope-down (F1)
function readCardHashSet(sp){if(!isReadablePointer(sp))return[];try{const klass=mono_object_get_class(sp);const fields=getFields(klass);let sO=-1,cO=-1,lO=-1;for(const f of fields){if(f.name==='_slots'||f.name==='m_slots')sO=f.offset;if(f.name==='_count'||f.name==='m_count')cO=f.offset;if(f.name==='_lastIndex'||f.name==='m_lastIndex')lO=f.offset;}if(sO<0){const dictCards=readCardDictionary(sp,fields);if(dictCards.length>0)return dictCards;logCollectionLayoutOnce('Unsupported card collection',klass,fields);return[];}const sa=safeReadPointer(sp,sO);if(!isReadablePointer(sa))return[];const count=cO>=0?sp.add(cO).readS32():0;const lastIdx=lO>=0?sp.add(lO).readS32():count;if(count<=0)return[];const ml=getManagedArrayLength(sa);const ss=4+4+Process.pointerSize;const base=getManagedArrayDataPtr(sa);if(!isReadablePointer(base)||ml<=0)return[];const cards=[];const lim=Math.min(ml,Math.max(lastIdx,count)+16,500);for(let i=0;i<lim&&cards.length<count;i++){try{const sb=base.add(i*ss);if(isReadableAddress(sb,4)&&sb.readS32()<0)continue;const vp=safeReadPointer(sb,8);if(vp){const card=readCardSnapshot(vp);if(card&&card.instance_id)cards.push(card);}}catch(e){}}if(cards.length===0&&count>0){logCardCollectionInfo('hashset-empty:'+(classFullName(klass)||'?'),'Card set '+(classFullName(klass)||'?')+' count='+count+' lastIndex='+lastIdx+' yielded 0 cards; fields: '+describeFieldLayout(fields));}return cards;}catch(e){send({type:'debug',msg:'readCardHashSet:'+e});return[];}}

function readEnumIntDict(dp, debugLabel, keepKeys, keepCount){if(!isReadablePointer(dp))return{};try{if(!mono_class_get_element_class||!mono_class_value_size){if(debugLabel)send({type:'debug',msg:'enum-int dict '+debugLabel+' missing mono helpers'});return{};}const dictKlass=mono_object_get_class(dp);const dictFields=getFields(dictKlass);const entriesField=findNamedField(dictFields,['_entries','entries']);const countField=findNamedField(dictFields,['_count','count']);if(!entriesField||!countField){if(debugLabel)logCollectionLayoutOnce('Unsupported enum-int dict '+debugLabel,dictKlass,dictFields);return{};}const entriesArray=safeReadPointer(dp,entriesField.offset);const count=dp.add(countField.offset).readS32();if(!isReadablePointer(entriesArray)){if(debugLabel)send({type:'debug',msg:'enum-int dict '+debugLabel+' entries array was null (count='+count+')'});return{};}if(count<=0){if(debugLabel)send({type:'debug',msg:'enum-int dict '+debugLabel+' count='+count});return{};}const arrayKlass=mono_object_get_class(entriesArray);if(!arrayKlass||arrayKlass.isNull()){if(debugLabel)send({type:'debug',msg:'enum-int dict '+debugLabel+' array klass was null'});return{};}const entryKlass=mono_class_get_element_class(arrayKlass);if(!entryKlass||entryKlass.isNull()){if(debugLabel)send({type:'debug',msg:'enum-int dict '+debugLabel+' entry klass was null'});return{};}const entryFields=getFields(entryKlass);const hashField=findNamedField(entryFields,['hashCode','_hashCode']);const keyField=findNamedField(entryFields,['key','Key']);const valueField=findNamedField(entryFields,['value','Value']);if(!keyField||!valueField){if(debugLabel)logDictionaryLayoutOnce('Unsupported enum-int dict '+debugLabel,dictKlass,dictFields,entryKlass,entryFields,{count:count,arrLen:'?',entrySize:'?'},[]);return{};}const align=Memory.alloc(4);align.writeU32(0);const entrySize=mono_class_value_size(entryKlass,align);if(!entrySize||entrySize<=0){if(debugLabel)send({type:'debug',msg:'enum-int dict '+debugLabel+' invalid entry size '+entrySize});return{};}const arrLen=getManagedArrayLength(entriesArray);const base=getManagedArrayDataPtr(entriesArray);if(!isReadablePointer(base)||arrLen<=0)return{};const result={};const samples=[];const limit=Math.min(arrLen,Math.max(count+16,count),500);const useFilter=!!keepKeys;let found=0;let kept=0;for(let i=0;i<limit&&found<count;i++){try{const entryBase=base.add(i*entrySize);if(hashField&&isReadableAddress(entryBase.add(hashField.offset),4)&&entryBase.add(hashField.offset).readS32()<0)continue;const key=readScalarField(entryBase,keyField);const value=readScalarField(entryBase,valueField);found++;if(useFilter&&!keepKeys[key])continue;result[key]=value;if(debugLabel&&samples.length<8)samples.push(key+':'+value);kept++;if(useFilter&&keepCount&&kept>=keepCount)break;}catch(e){}}if(debugLabel)logDictionaryLayoutOnce(debugLabel,dictKlass,dictFields,entryKlass,entryFields,{count:count,arrLen:arrLen,entrySize:entrySize},samples);if(!_playerAttrsDictLayout&&found>0&&entrySize>0){const headerAdj=Math.min(hashField?hashField.offset:999,keyField.offset,valueField.offset);_playerAttrsDictLayout={entriesOff:entriesField.offset,countOff:countField.offset,entrySize:entrySize,hashOff:hashField?(hashField.offset-headerAdj):null,keyOff:keyField.offset-headerAdj,valueOff:valueField.offset-headerAdj,headerAdj:headerAdj};send({type:'info',msg:'QW10 dict layout cached: entriesOff='+entriesField.offset+' countOff='+countField.offset+' entrySize='+entrySize+' hashOff='+(hashField?(hashField.offset-headerAdj):'null')+' keyOff='+(keyField.offset-headerAdj)+' valueOff='+(valueField.offset-headerAdj)+' headerAdj='+headerAdj});}return result;}catch(e){if(debugLabel)send({type:'debug',msg:'readEnumIntDict '+debugLabel+': '+e});return{};}}

function readDynamicRunSnapshot(p){if(!p||p.isNull())return{};const r={};try{const gameModeId=readDynamicGuidField(p,['GameModeId']);if(gameModeId)r.game_mode_id=gameModeId;const day=readDynamicU32Field(p,['Day']);if(day!==null)r.day=day;const hour=readDynamicU32Field(p,['Hour']);if(hour!==null)r.hour=hour;const victories=readDynamicU32Field(p,['Victories']);if(victories!==null)r.victories=victories;const defeats=readDynamicU32Field(p,['Defeats']);if(defeats!==null)r.defeats=defeats;const visitedFates=readDynamicBoolField(p,['HasVisitedFates']);if(visitedFates!==null)r.visited_fates=visitedFates;const dataVersion=readDynamicStringField(p,['DataVersion']);if(dataVersion!==null)r.data_version=dataVersion;}catch(e){send({type:'debug',msg:'readDynRun:'+e});}return r;}

function readDynamicRunStateSnapshot(p){if(!p||p.isNull())return{};const r={};try{const stateInt=readDynamicI32Field(p,['StateName']);if(stateInt!==null){r.state=E_RUN_STATE[stateInt]||('Unknown('+stateInt+')');r.state_int=stateInt;}
// Re-enabled from W/L-only scope-down (F5)
const encounterId=readDynamicStringField(p,['CurrentEncounterId']);if(encounterId!==null)r.current_encounter_id=encounterId;
const rerollCost=readDynamicNullableU32Field(p,['RerollCost']);if(rerollCost!==null)r.reroll_cost=rerollCost;
const rerollsRemaining=readDynamicNullableU32Field(p,['RerollsRemaining']);if(rerollsRemaining!==null)r.rerolls_remaining=rerollsRemaining;
// F4: selection_set gated to interesting states
if(ACTION_CARD_STATES[r.state]){const selectionSetPtr=readDynamicObjectField(p,['SelectionSet']);if(selectionSetPtr&&!selectionSetPtr.isNull())r.selection_set=readStringList(selectionSetPtr);}
}catch(e){send({type:'debug',msg:'readDynState:'+e});}return r;}

function readDynamicPlayerSnapshot(p, includeAttributes){if(!p||p.isNull())return{};const r={};try{const heroInt=readDynamicI32Field(p,['Hero']);if(heroInt!==null)r.hero=E_HERO[heroInt]||('Unknown('+heroInt+')');
// Re-enabled from W/L-only scope-down (F10)
const unlockedSlots=readDynamicU16Field(p,['UnlockedSlots']);if(unlockedSlots!==null)r.unlocked_slots=unlockedSlots;
// Keep only the live HUD / enrichment attributes to reduce delta cost.
if(includeAttributes){const attrsPtr=readDynamicObjectField(p,['Attributes']);if(attrsPtr&&!attrsPtr.isNull()){const attrs=readEnumIntDict(attrsPtr,'DynamicPlayer.Attributes',KEEP_PLAYER_ATTR_IDS,KEEP_PLAYER_ATTR_COUNT);for(const[k,v]of Object.entries(attrs))r[E_PLAYER_ATTRIBUTE[parseInt(k)]||('attr_'+k)]=v;}else send({type:'debug',msg:'DynamicPlayer.Attributes pointer was null'});}
}catch(e){send({type:'debug',msg:'readDynPlayer:'+e});}return r;}

function readDynamicPlayerLean(p){if(!p||p.isNull())return{};const r={};try{const heroInt=readDynamicI32Field(p,['Hero']);if(heroInt!==null)r.hero=E_HERO[heroInt]||('Unknown('+heroInt+')');const unlockedSlots=readDynamicU16Field(p,['UnlockedSlots']);if(unlockedSlots!==null)r.unlocked_slots=unlockedSlots;}catch(e){send({type:'debug',msg:'readDynPlayerLean:'+e});}return r;}

// =====================================================================
// QW9: Fast GameSim path — all five optimizations in one block
// =====================================================================

// QW9-FIX3: Batch field reader. Resolves all field offsets for a class once,
// caches them, and reads multiple fields from the same object in a single pass
// without repeated mono_object_get_class / getDynamicFieldsForKlass calls.
//
// QW10: Uses direct pointer read for class lookup instead of mono_object_get_class
// NativeFunction call. In Mono, the class pointer is stored at object+0 (the vtable
// pointer, first field of MonoObject). We read it directly to avoid the ~3-5ms
// NativeFunction bridge overhead.
const _klassNameCache = new Map(); // klass ptr → className string
function _getBatchOffsets(objPtr) {
    try {
        if (!objPtr || objPtr.isNull()) return null;
        // QW10: direct class pointer read — avoids mono_object_get_class NativeFunction.
        // MonoObject layout: { MonoVTable *vtable; ... }
        // MonoVTable layout: { MonoClass *klass; ... }
        // So klass = *(*(objPtr + 0) + 0) — double dereference.
        const vtable = objPtr.readPointer();
        if (!vtable || vtable.isNull()) return null;
        const klass = vtable.readPointer();
        if (!klass || klass.isNull()) return null;
        const klassKey = klass.toString();
        // Check if we already have the field map for this class pointer
        let className = _klassNameCache.get(klassKey);
        if (className === undefined) {
            // First time seeing this class pointer — resolve name via Mono API (once)
            className = classFullName(klass);
            _klassNameCache.set(klassKey, className || '');
        }
        if (!className) return null;
        if (_batchFieldOffsetCache[className]) return _batchFieldOffsetCache[className];
        const fields = getDynamicFieldsForKlass(klass);
        const map = {};
        for (const f of fields) {
            if (f && f.name) map[f.name] = f;
        }
        _batchFieldOffsetCache[className] = map;
        return map;
    } catch (e) { return null; }
}

// QW10: Direct pointer reads — eliminates mono_field_get_value NativeFunction calls.
// For reference-type fields in Mono managed objects, the field value is a pointer
// stored at (objPtr + field.offset). We read it directly instead of calling
// mono_field_get_value which has ~3-5ms overhead per call on Windows.
// Safe because we're on the game thread (GC can't move objects during our hook).
function _fastReadI32(objPtr, fieldMap, name) {
    try {
        const f = fieldMap[name];
        if (!f) return null;
        return objPtr.add(f.offset).readS32();
    } catch (e) { return null; }
}
function _fastReadU32(objPtr, fieldMap, name) {
    try {
        const f = fieldMap[name];
        if (!f) return null;
        return objPtr.add(f.offset).readU32();
    } catch (e) { return null; }
}
function _fastReadU16(objPtr, fieldMap, name) {
    try {
        const f = fieldMap[name];
        if (!f) return null;
        return objPtr.add(f.offset).readU16();
    } catch (e) { return null; }
}
function _fastReadBool(objPtr, fieldMap, name) {
    try {
        const f = fieldMap[name];
        if (!f) return null;
        return objPtr.add(f.offset).readU8() !== 0;
    } catch (e) { return null; }
}
function _fastReadObjPtr(objPtr, fieldMap, name) {
    try {
        const f = fieldMap[name];
        if (!f) return null;
        // QW10: direct pointer read at offset — replaces mono_field_get_value + Memory.alloc
        const value = objPtr.add(f.offset).readPointer();
        return (value && !value.isNull()) ? value : null;
    } catch (e) { return null; }
}
function _fastReadString(objPtr, fieldMap, name) {
    try {
        const strPtr = _fastReadObjPtr(objPtr, fieldMap, name);
        return strPtr ? _directReadMonoString(strPtr) : null;
    } catch (e) { return null; }
}
function _fastReadGuid(objPtr, fieldMap, name) {
    try {
        const f = fieldMap[name];
        if (!f) return null;
        return readGuid(objPtr, f.offset);
    } catch (e) { return null; }
}
function _fastReadNullableU32(objPtr, fieldMap, name) {
    try {
        const f = fieldMap[name];
        if (!f) return null;
        const base = objPtr.add(f.offset);
        return base.readU8() ? base.add(4).readU32() : null;
    } catch (e) { return null; }
}

// QW10: Fast player attrs reader — uses cached dictionary layout for pure direct
// memory reads with ZERO NativeFunction calls. After readEnumIntDict succeeds once
// and populates _playerAttrsDictLayout, this function replaces it entirely.
// Reads the managed Dictionary<PlayerAttribute, int> entries array directly:
//   dict + entriesOff → entries array pointer
//   dict + countOff → entry count
//   entries + MONO_ARRAY_HEADER → data start
//   data + i*entrySize + keyOff/valueOff → key/value pairs
// Skips tombstones (hashCode < 0). Returns the same {key: value} format.
const MONO_ARRAY_HEADER_64 = 16; // MonoArray header: 8 (vtable) + 4 (max_length) + 4 (pad)
function _fastReadPlayerAttrs(dictPtr, keepKeys, keepCount) {
    const layout = _playerAttrsDictLayout;
    if (!layout || !dictPtr) return null;
    try {
        // Read entries array pointer directly from dict object
        const entriesArray = dictPtr.add(layout.entriesOff).readPointer();
        if (!entriesArray || entriesArray.isNull()) { _fastAttrsFailCount++; return null; }
        const count = dictPtr.add(layout.countOff).readS32();
        if (count <= 0 || count > 500) { _fastAttrsFailCount++; return null; }
        // Array data starts after managed array header
        const base = entriesArray.add(MONO_ARRAY_HEADER_64);
        const result = {};
        let found = 0;
        let kept = 0;
        const limit = Math.min(count + 16, 500);
        for (let i = 0; i < limit && found < count; i++) {
            const entryBase = base.add(i * layout.entrySize);
            // Check tombstone
            if (layout.hashOff !== null) {
                const hash = entryBase.add(layout.hashOff).readS32();
                if (hash < 0) continue;
            }
            const key = entryBase.add(layout.keyOff).readS32();
            found++;
            if (keepKeys && !keepKeys[key]) continue;
            const value = entryBase.add(layout.valueOff).readS32();
            result[key] = value;
            kept++;
            if (keepCount && kept >= keepCount) break;
        }
        _fastAttrsReadCount++;
        return result;
    } catch (e) {
        _fastAttrsFailCount++;
        return null;
    }
}

// QW10: Content-hash SelectionSet cache. The game allocates a new array each
// tick (pointer always changes), so pointer-identity caching never hits.
// Instead, fingerprint the array by reading the raw element pointers — if the
// same string objects are referenced in the same order, the content is identical.
// This avoids mono_string_to_utf8 + mono_free calls (2 NativeFunction calls per string).
let _lastSelSetFingerprint = null;
function _readSelectionSetCached(statePtr, fieldMap) {
    try {
        const selPtr = _fastReadObjPtr(statePtr, fieldMap, 'SelectionSet');
        if (!selPtr || selPtr.isNull()) return [];
        // Managed array: header(16 bytes) then element pointers
        const len = selPtr.add(8).readS32(); // max_length at offset 8 in MonoArray
        if (len <= 0 || len > 1000) return [];
        // Build fingerprint from element pointers (8 bytes each on 64-bit)
        const dataStart = selPtr.add(MONO_ARRAY_HEADER_64);
        let fingerprint = '' + len;
        const fpLen = Math.min(len, 8); // fingerprint first 8 elements max
        for (let i = 0; i < fpLen; i++) {
            fingerprint += '|' + dataStart.add(i * Process.pointerSize).readPointer().toString();
        }
        if (fingerprint === _lastSelSetFingerprint) {
            _selectionSetCacheHits++;
            return _lastSelectionSetResult;
        }
        // Cache miss: decode strings directly (no readStringList → readMonoString)
        _selectionSetCacheMisses++;
        const result = [];
        for (let i = 0; i < len; i++) {
            const ep = dataStart.add(i * Process.pointerSize).readPointer();
            if (ep && !ep.isNull()) result.push(_directReadMonoString(ep));
        }
        _lastSelSetFingerprint = fingerprint;
        _lastSelectionSetResult = result;
        return result;
    } catch (e) { return []; }
}

// QW9-FIX1+2: Unified GameSim reader. Merges readDynamicStateLean +
// readDynamicStatePayload into a single pass. Reads Run/State/Player exactly
// once. Player attributes are read synchronously but throttled to state changes
// (eliminates the 87% deferred-failure cascade).
function readGameSimFast(dataPtr, includeCards, includePlayerAttrs, includeTemplateEvents) {
    if (!dataPtr || dataPtr.isNull()) return null;
    const r = {run:{}, state:{}, player:{}, offered:[], player_board:[], player_stash:[], player_skills:[], opponent_board:[]};
    let sawAny = false;
    try {
        // --- Batch-read dataPtr fields once ---
        const dataFields = _getBatchOffsets(dataPtr);
        if (!dataFields) return null;

        // --- Run ---
        const runPtr = _fastReadObjPtr(dataPtr, dataFields, 'Run');
        if (runPtr) {
            const runFields = _getBatchOffsets(runPtr);
            if (runFields) {
                const day = _fastReadU32(runPtr, runFields, 'Day');
                if (day !== null) r.run.day = day;
                const hour = _fastReadU32(runPtr, runFields, 'Hour');
                if (hour !== null) r.run.hour = hour;
                const victories = _fastReadU32(runPtr, runFields, 'Victories');
                if (victories !== null) r.run.victories = victories;
                const defeats = _fastReadU32(runPtr, runFields, 'Defeats');
                if (defeats !== null) r.run.defeats = defeats;
                const gameModeId = _fastReadGuid(runPtr, runFields, 'GameModeId');
                if (gameModeId) r.run.game_mode_id = gameModeId;
                const visitedFates = _fastReadBool(runPtr, runFields, 'HasVisitedFates');
                if (visitedFates !== null) r.run.visited_fates = visitedFates;
                // QW10: DataVersion is static per run — cache after first read
                if (_cachedDataVersion !== null) {
                    r.run.data_version = _cachedDataVersion;
                } else {
                    const dataVersion = _fastReadString(runPtr, runFields, 'DataVersion');
                    if (dataVersion !== null) { r.run.data_version = dataVersion; _cachedDataVersion = dataVersion; }
                }
                sawAny = true;
            }
        }

        // --- State ---
        const statePtr = _fastReadObjPtr(dataPtr, dataFields, 'CurrentState');
        if (statePtr) {
            const stateFields = _getBatchOffsets(statePtr);
            if (stateFields) {
                const stateInt = _fastReadI32(statePtr, stateFields, 'StateName');
                if (stateInt !== null) {
                    r.state.state = E_RUN_STATE[stateInt] || ('Unknown(' + stateInt + ')');
                    r.state.state_int = stateInt;
                }
                // QW10: Only read CurrentEncounterId string on action states (saves 2 NativeFunction calls)
                if (ACTION_CARD_STATES[r.state.state]) {
                    const encounterId = _fastReadString(statePtr, stateFields, 'CurrentEncounterId');
                    if (encounterId !== null) r.state.current_encounter_id = encounterId;
                }
                const rerollCost = _fastReadNullableU32(statePtr, stateFields, 'RerollCost');
                if (rerollCost !== null) r.state.reroll_cost = rerollCost;
                const rerollsRemaining = _fastReadNullableU32(statePtr, stateFields, 'RerollsRemaining');
                if (rerollsRemaining !== null) r.state.rerolls_remaining = rerollsRemaining;
                // QW9-FIX5: cached SelectionSet — only decode strings on pointer change
                if (ACTION_CARD_STATES[r.state.state]) {
                    r.state.selection_set = _readSelectionSetCached(statePtr, stateFields);
                }
                sawAny = true;
            }
        }

        // --- Player ---
        const playerPtr = _fastReadObjPtr(dataPtr, dataFields, 'Player');
        if (playerPtr) {
            const playerFields = _getBatchOffsets(playerPtr);
            if (playerFields) {
                const heroInt = _fastReadI32(playerPtr, playerFields, 'Hero');
                if (heroInt !== null) r.player.hero = E_HERO[heroInt] || ('Unknown(' + heroInt + ')');
                const unlockedSlots = _fastReadU16(playerPtr, playerFields, 'UnlockedSlots');
                if (unlockedSlots !== null) r.player.unlocked_slots = unlockedSlots;

                // QW10: Fast player attrs — uses cached dict layout for direct memory
                // reads (ZERO NativeFunction calls). Falls back to readEnumIntDict
                // only until the layout is cached on first successful read.
                // Throttled: only attempt on state change or interval expiry.
                // Cached attrs applied on every snapshot regardless.
                if (includePlayerAttrs && ATTRS_THROTTLE_ON_STATE_CHANGE) {
                    const now = Date.now();
                    const currStateName = r.state.state || null;
                    const stateChanged = currStateName !== _lastAttrsSyncState;
                    const intervalElapsed = (now - _lastAttrsSyncMs) >= ATTRS_SYNC_MIN_INTERVAL_MS;
                    let freshAttrs = null;
                    if (stateChanged || intervalElapsed) {
                        const attrsPtr = _fastReadObjPtr(playerPtr, playerFields, 'Attributes');
                        if (attrsPtr) {
                            let attrsDict = null;
                            // Use fast direct reader if layout is cached, else slow path
                            if (_playerAttrsDictLayout) {
                                attrsDict = _fastReadPlayerAttrs(attrsPtr, KEEP_PLAYER_ATTR_IDS, KEEP_PLAYER_ATTR_COUNT);
                            }
                            if (!attrsDict) {
                                // Slow path — also populates _playerAttrsDictLayout on success
                                attrsDict = readEnumIntDict(attrsPtr, null, KEEP_PLAYER_ATTR_IDS, KEEP_PLAYER_ATTR_COUNT);
                            }
                            if (attrsDict) {
                                const resolved = {};
                                for (const [k, v] of Object.entries(attrsDict)) {
                                    resolved[E_PLAYER_ATTRIBUTE[parseInt(k)] || ('attr_' + k)] = v;
                                }
                                if (Object.keys(resolved).length > 0) {
                                    freshAttrs = resolved;
                                    _lastGoodAttrs = resolved;
                                } else {
                                    _attrsSyncEmptyCount++;
                                }
                            }
                            _attrsSyncReadCount++;
                        }
                        _lastAttrsSyncMs = now;
                        _lastAttrsSyncState = currStateName;
                    } else {
                        _attrsSyncThrottledCount++;
                    }
                    // Apply: fresh result if we got one, otherwise last known good
                    const attrsToApply = freshAttrs || _lastGoodAttrs;
                    if (attrsToApply) {
                        for (const [k, v] of Object.entries(attrsToApply)) {
                            r.player[k] = v;
                        }
                        if (!freshAttrs) _attrsFromCacheCount++;
                    }
                } else if (includePlayerAttrs) {
                    // Legacy deferred path (ATTRS_THROTTLE_ON_STATE_CHANGE = false)
                    const wantAttrsSync = _pendingSyncAttrsRead;
                    if (wantAttrsSync) { _pendingSyncAttrsRead = false; _syncAttrsFallbackCount++; }
                    if (wantAttrsSync) {
                        const attrsPtr = _fastReadObjPtr(playerPtr, playerFields, 'Attributes');
                        if (attrsPtr) {
                            const attrs = readEnumIntDict(attrsPtr, null, KEEP_PLAYER_ATTR_IDS, KEEP_PLAYER_ATTR_COUNT);
                            for (const [k, v] of Object.entries(attrs)) {
                                r.player[E_PLAYER_ATTRIBUTE[parseInt(k)] || ('attr_' + k)] = v;
                            }
                        }
                    } else {
                        dispatchDeferredPlayerAttrs(playerPtr, snapshotCounter + 1);
                    }
                }
                sawAny = true;
            }
        }

        // --- Template events (deferred, gated by state) ---
        if (includeTemplateEvents && shouldReadActionTemplateEvents(r)) {
            const eventsPtr = _fastReadObjPtr(dataPtr, dataFields, 'Events');
            if (eventsPtr) {
                const _snapshotId = snapshotCounter + 1;
                setImmediate(function() {
                    try {
                        const templateEvents = readGameSimTemplateEventsFromList(eventsPtr);
                        if (templateEvents.length > 0) {
                            send({type:'deferred_template_events', snapshot_id:_snapshotId, card_template_events:templateEvents});
                        }
                    } catch (e) { send({type:'debug', msg:'deferred-template-events:' + e}); }
                });
            }
        }

        // --- Cards (deferred, gated by state) ---
        const _wantCards = includeCards && (FULL_DELTA_CARDS ? shouldReadHeavyCards(r, false) : shouldReadActionCards(r));
        if (_wantCards) {
            const cardRef = findCardCollectionField(dataPtr, ['Cards']);
            const cardCollectionPtr = cardRef ? cardRef.ptr : null;
            if (cardCollectionPtr && !cardCollectionPtr.isNull()) {
                const _snapshotId = snapshotCounter + 1;
                setImmediate(function() {
                    try {
                        const cards = readCardHashSet(cardCollectionPtr);
                        if (cards.length > 0) {
                            const deferred = {offered:[], player_board:[], player_stash:[], player_skills:[], opponent_board:[]};
                            for (const c of cards) bucketCard(c, deferred);
                            send({type:'deferred_cards', snapshot_id:_snapshotId, cards:deferred});
                        } else {
                            logCardCollectionInfo('deferred-fast-empty:' + (cardRef.className || '?'),
                                'Fast-path deferred card collection ' + cardRef.className + ' yielded 0 cards');
                        }
                    } catch (e) { send({type:'debug', msg:'deferred-fast-cards:' + e}); }
                });
                sawAny = true;
            }
        }
    } catch (e) {
        send({type:'debug', msg:'readGameSimFast:' + e});
    }
    return sawAny ? r : null;
}

// =====================================================================
// End QW9
// =====================================================================

function cloneDynamicSnapshotBase(baseSnapshot){return{run:Object.assign({},(baseSnapshot&&baseSnapshot.run)||{}),state:Object.assign({},(baseSnapshot&&baseSnapshot.state)||{}),player:Object.assign({},(baseSnapshot&&baseSnapshot.player)||{}),offered:[],player_board:[],player_stash:[],player_skills:[],opponent_board:[]};}

function hasInterestingSelectionSet(state){return !!(state&&state.selection_set&&state.selection_set.some(v=>v!==null&&v!==''));}
function selectionSetValues(stateObj){
    return (stateObj&&Array.isArray(stateObj.selection_set)) ? stateObj.selection_set : [];
}
function isMapLikeSelectionId(value){
    const v=String(value||'');
    return v.startsWith('enc_')||v.startsWith('ste_')||v.startsWith('com_')||v.startsWith('ped_')||v.startsWith('pvp_');
}
function isCardLikeSelectionId(value){
    const v=String(value||'');
    return v.startsWith('itm_')||v.startsWith('skl_')||v.startsWith('com_');
}

function shouldAllowInlineCardRead(snapshot, forceFull){
    if(FULL_DELTA_CARDS) return true;
    if(!snapshot) return false;
    const stateName=snapshot.state&&snapshot.state.state;
    if(!stateName||!INLINE_CARD_STATES[stateName]) return false;
    const selection=selectionSetValues(snapshot.state);
    if(selection.length===0) return false;
    if(selection.length>MAX_INLINE_CARD_COUNT) return false;
    const hasMapLike=selection.some(isMapLikeSelectionId);
    if(hasMapLike) return false;
    const hasCardLike=selection.some(isCardLikeSelectionId);
    if(!hasCardLike && stateName !== "LevelUp" && stateName !== "Loot") return false;
    if(forceFull && (stateName === "EndRunVictory" || stateName === "EndRunDefeat")) return true;
    return true;
}
function shouldForceHeavySnapshot(snapshot){
    if(!snapshot) return false;
    const stateName=snapshot.state&&snapshot.state.state;
    if(stateName&&HEAVY_CARD_STATES[stateName]) return true;
    if(hasInterestingSelectionSet(snapshot.state)) return true;
    return false;
}
function shouldReadHeavyCards(snapshot, forceFull){
    if(forceFull) return true;
    if(!snapshot||!snapshot.state) return false;
    const stateName=snapshot.state.state||snapshot.state;
    return !!(HEAVY_CARD_STATES[stateName]);
}
function shouldReadActionCards(snapshot){
    if(!ACTION_EVENT_CARDS) return false;
    if(!snapshot||!snapshot.state) return false;
    const stateName=snapshot.state.state||snapshot.state;
    return !!(ACTION_CARD_STATES[stateName]);
}
function shouldReadActionTemplateEvents(snapshot){
    if(!snapshot||!snapshot.state) return false;
    const stateName=snapshot.state.state||snapshot.state;
    return !!(ACTION_TEMPLATE_EVENT_STATES[stateName]);
}

// Re-enabled from W/L-only scope-down (F5): restored reroll cost, rerolls remaining, and selection_set.
function readDynamicStateLean(dataPtr){if(!dataPtr||dataPtr.isNull())return null;const r={run:{},state:{},player:{},offered:[],player_board:[],player_stash:[],player_skills:[],opponent_board:[]};let sawAny=false;try{const runPtr=readDynamicObjectField(dataPtr,['Run']);if(runPtr&&!runPtr.isNull()){const day=readDynamicU32Field(runPtr,['Day']);if(day!==null)r.run.day=day;const hour=readDynamicU32Field(runPtr,['Hour']);if(hour!==null)r.run.hour=hour;const victories=readDynamicU32Field(runPtr,['Victories']);if(victories!==null)r.run.victories=victories;const defeats=readDynamicU32Field(runPtr,['Defeats']);if(defeats!==null)r.run.defeats=defeats;sawAny=true;}const statePtr=readDynamicObjectField(dataPtr,['CurrentState']);if(statePtr&&!statePtr.isNull()){const stateInt=readDynamicI32Field(statePtr,['StateName']);if(stateInt!==null){r.state.state=E_RUN_STATE[stateInt]||('Unknown('+stateInt+')');r.state.state_int=stateInt;}const rerollCost=readDynamicNullableU32Field(statePtr,['RerollCost']);if(rerollCost!==null)r.state.reroll_cost=rerollCost;const rerollsRemaining=readDynamicNullableU32Field(statePtr,['RerollsRemaining']);if(rerollsRemaining!==null)r.state.rerolls_remaining=rerollsRemaining;const selectionSetPtr=readDynamicObjectField(statePtr,['SelectionSet']);if(selectionSetPtr&&!selectionSetPtr.isNull())r.state.selection_set=readStringList(selectionSetPtr);sawAny=true;}const playerPtr=readDynamicObjectField(dataPtr,['Player']);if(playerPtr&&!playerPtr.isNull()){r.player=readDynamicPlayerLean(playerPtr);sawAny=true;}}catch(e){send({type:'debug',msg:'readDynamicStateLean:'+e});}return sawAny?r:null;}

// Defer heavy Player.Attributes enumeration off the game thread.
// The managed dict walk (20-38 entries * range-checked memory reads) dominates
// hook latency on NetMessageGameSim — 91% of slow hooks. Capture the pointer
// cheaply on the game thread, decode in setImmediate, ship as deferred_player_attrs.
// On empty/exception, flip _pendingSyncAttrsRead so the next hook falls back to
// sync and we don't silently drop attrs forever.
//
// HITCHING FOLLOW-UP (roadmap section 5, Short-Term): if live runs show
// remaining GameSim hitching even with this deferred path in place,
// throttle the dispatch here — e.g., skip calling dispatchDeferredPlayerAttrs
// when (Date.now() - _lastDeferredAttrsDispatchMs) < N ms, or coalesce
// pointer captures by snapshot state. Do not speculatively throttle
// without a reproducible hitch — KEEP_PLAYER_ATTR_IDS already narrows the
// managed dict walk to 5 live attrs (Gold, Health, HealthMax, Level,
// Prestige), which is the cheapest read that still feeds the overlay.
function dispatchDeferredPlayerAttrs(playerPtr,snapshotId){try{const attrsPtr=readDynamicObjectField(playerPtr,['Attributes']);if(!attrsPtr||attrsPtr.isNull())return false;setImmediate(function(){try{const attrsDict=readEnumIntDict(attrsPtr,null,KEEP_PLAYER_ATTR_IDS,KEEP_PLAYER_ATTR_COUNT);const attrs={};for(const[k,v]of Object.entries(attrsDict))attrs[E_PLAYER_ATTRIBUTE[parseInt(k)]||('attr_'+k)]=v;const attrCount=Object.keys(attrs).length;if(attrCount>0){_deferredAttrsSuccessCount++;send({type:'deferred_player_attrs',snapshot_id:snapshotId,attrs:attrs});}else{_deferredAttrsFailureCount++;_pendingSyncAttrsRead=true;}maybeReportAttrsStats();}catch(e){_deferredAttrsFailureCount++;_pendingSyncAttrsRead=true;send({type:'debug',msg:'deferred-player-attrs:'+e});}});return true;}catch(e){return false;}}

function maybeReportAttrsStats(){const now=Date.now();if(now-_lastAttrsStatReportMs<ATTRS_STAT_REPORT_INTERVAL_MS)return;_lastAttrsStatReportMs=now;if(FAST_GAMESIM_PATH&&ATTRS_THROTTLE_ON_STATE_CHANGE){send({type:'info',msg:'QW10 attrs stats: sync_reads='+_attrsSyncReadCount+' throttled='+_attrsSyncThrottledCount+' empty='+_attrsSyncEmptyCount+' from_cache='+_attrsFromCacheCount+' fast_dict='+_fastAttrsReadCount+' fast_dict_fail='+_fastAttrsFailCount+' dict_layout='+(!!_playerAttrsDictLayout)+' selset_hits='+_selectionSetCacheHits+' selset_misses='+_selectionSetCacheMisses});return;}const total=_deferredAttrsSuccessCount+_deferredAttrsFailureCount;if(total===0)return;const failureRate=(_deferredAttrsFailureCount/total*100).toFixed(1);send({type:'info',msg:'deferred_player_attrs stats: success='+_deferredAttrsSuccessCount+' failure='+_deferredAttrsFailureCount+' sync_fallback='+_syncAttrsFallbackCount+' failure_rate='+failureRate+'%'});}

// Re-enabled from W/L-only scope-down (F1): deferred card decode via setImmediate
// includeCards=true now grabs only the collection pointer on the game thread, then defers heavy decode
function readDynamicStatePayload(dataPtr, includeCards, includePlayerAttrs, includeTemplateEvents, baseSnapshot){if(!dataPtr||dataPtr.isNull())return null;const r=cloneDynamicSnapshotBase(baseSnapshot);let sawAny=!!baseSnapshot;try{const runPtr=readDynamicObjectField(dataPtr,['Run']);if(runPtr&&!runPtr.isNull()){if(!baseSnapshot||!baseSnapshot.run||Object.keys(baseSnapshot.run).length===0)r.run=readDynamicRunSnapshot(runPtr);sawAny=true;}const statePtr=readDynamicObjectField(dataPtr,['CurrentState']);if(statePtr&&!statePtr.isNull()){if(!baseSnapshot||!baseSnapshot.state||Object.keys(baseSnapshot.state).length===0){r.state=readDynamicRunStateSnapshot(statePtr);}else{const encounterId=readDynamicStringField(statePtr,['CurrentEncounterId']);if(encounterId!==null)r.state.current_encounter_id=encounterId;}sawAny=true;}const playerPtr=readDynamicObjectField(dataPtr,['Player']);if(playerPtr&&!playerPtr.isNull()){const needFullPlayer=includePlayerAttrs||!baseSnapshot||!baseSnapshot.player||Object.keys(baseSnapshot.player).length===0;if(needFullPlayer){const wantAttrsSync=includePlayerAttrs&&_pendingSyncAttrsRead;if(wantAttrsSync){_pendingSyncAttrsRead=false;_syncAttrsFallbackCount++;}r.player=Object.assign({},r.player,readDynamicPlayerSnapshot(playerPtr,wantAttrsSync));if(includePlayerAttrs&&!wantAttrsSync)dispatchDeferredPlayerAttrs(playerPtr,snapshotCounter+1);}sawAny=true;}if(includeTemplateEvents){const eventsPtr=readDynamicObjectField(dataPtr,['Events']);if(eventsPtr&&!eventsPtr.isNull()){const _snapshotId=snapshotCounter+1;setImmediate(function(){try{const templateEvents=readGameSimTemplateEventsFromList(eventsPtr);if(templateEvents.length>0){send({type:'deferred_template_events',snapshot_id:_snapshotId,card_template_events:templateEvents});}}catch(e){send({type:'debug',msg:'deferred-template-events:'+e});}});}}if(includeCards){// Capture-and-release: grab pointer on game thread, decode in setImmediate
const cardRef=findCardCollectionField(dataPtr,['Cards']);const cardCollectionPtr=cardRef?cardRef.ptr:null;if(cardCollectionPtr&&!cardCollectionPtr.isNull()){const _snapshotId=snapshotCounter+1;// will match snap.id after hookMethod increments
setImmediate(function(){try{const cards=readCardHashSet(cardCollectionPtr);if(cards.length>0){const deferred={offered:[],player_board:[],player_stash:[],player_skills:[],opponent_board:[]};for(const c of cards)bucketCard(c,deferred);send({type:'deferred_cards',snapshot_id:_snapshotId,cards:deferred});}else{logCardCollectionInfo('deferred-dynamic-empty:'+(cardRef.className||'?'),'Deferred dynamic card collection '+cardRef.className+' yielded 0 cards');}}catch(e){send({type:'debug',msg:'deferred-dynamic-cards:'+e});}});sawAny=true;}}}catch(e){send({type:'debug',msg:'readDynamicStatePayload:'+e});}return sawAny?r:null;}

// Re-enabled from W/L-only scope-down (F1): deferred card decode via setImmediate
function readGameStateSnapshot(sp, includeCards){if(!sp||sp.isNull())return null;const r={run:{},state:{},player:{},offered:[],player_board:[],player_stash:[],player_skills:[],opponent_board:[]};try{const runPtr=readObjectField(sp,'GameStateSnapshotDTO','Run');const statePtr=readObjectField(sp,'GameStateSnapshotDTO','CurrentState');const playerPtr=readObjectField(sp,'GameStateSnapshotDTO','Player');if(runPtr&&!runPtr.isNull())r.run=readRunSnapshot(runPtr);else logNullField('GameStateSnapshotDTO.Run','was null');if(statePtr&&!statePtr.isNull())r.state=readRunStateSnapshot(statePtr);else logNullField('GameStateSnapshotDTO.CurrentState','was null');if(playerPtr&&!playerPtr.isNull())r.player=readPlayerSnapshot(playerPtr);else logNullField('GameStateSnapshotDTO.Player','was null');if(includeCards){// Capture-and-release: grab pointer on game thread, decode in setImmediate
const cardRef=findCardCollectionField(sp,['Cards']);const cardCollectionPtr=cardRef?cardRef.ptr:null;if(cardCollectionPtr&&!cardCollectionPtr.isNull()){const _snapshotId=snapshotCounter+1;// will match snap.id after hookMethod increments
setImmediate(function(){try{const cards=readCardHashSet(cardCollectionPtr);if(cards.length>0){const deferred={offered:[],player_board:[],player_stash:[],player_skills:[],opponent_board:[]};for(const c of cards)bucketCard(c,deferred);send({type:'deferred_cards',snapshot_id:_snapshotId,cards:deferred});}else{logCardCollectionInfo('deferred-snapshot-empty:'+(cardRef.className||'?'),'Deferred snapshot card collection '+(cardRef.className||'?')+' yielded 0 cards');}}catch(e){send({type:'debug',msg:'deferred-snapshot-cards:'+e});}});}else logNullField('GameStateSnapshotDTO.Cards','was null');}}catch(e){send({type:'debug',msg:'readSnapshot:'+e});}return r;}

// Hooking

// QW3: Cache getArgClassName by argPtr string (same method always has same param types)
const _argClassNameCache = new Map();
function getArgClassName(argPtr){
    try{
        if(!argPtr||argPtr.isNull())return null;
        const key=argPtr.toString();
        const cached=_argClassNameCache.get(key);
        if(cached!==undefined)return cached||null;
        if(Process.findRangeByAddress(argPtr)===null){_argClassNameCache.set(key,'');return null;}
        const klass=mono_object_get_class(argPtr);
        if(!klass||klass.isNull()){_argClassNameCache.set(key,'');return null;}
        if(Process.findRangeByAddress(klass)===null){_argClassNameCache.set(key,'');return null;}
        const name=classFullName(klass);
        _argClassNameCache.set(key,name||'');
        return name;
    }catch(e){return null;}
}

function inspectArgs(method,args){const matches=[];const maxArgs=Math.min(6,Math.max(3,method.paramCount+2));for(let i=0;i<maxArgs;i++){const argPtr=args[i];const className=getArgClassName(argPtr);if(className)matches.push({index:i,ptr:argPtr,className:className});}const key=method.name+'/'+method.paramCount;if(matches.length>0&&(argLogCounts[key]||0)<5){argLogCounts[key]=(argLogCounts[key]||0)+1;send({type:'debug',msg:formatMethod(method)+' arg objects: '+matches.map(m=>'arg'+m.index+'='+m.className).join(', ')});}return matches;}

function buildSnapshotHints(method){
    const hints=[];
    for(let i=0;i<method.params.length;i++){
        const paramType=method.params[i]||'';
        if(paramType.includes('NetMessageGameStateSync')||paramType.includes('GameStateSnapshot')||paramType.includes('NetMessageCombatSim')||paramType.includes('NetMessageGameSim')||paramType.includes('NetMessageRunInitialized')){
            hints.push({runtimeIndex:i+1,paramType:paramType});
        }
    }
    return hints;
}

function buildCommandHints(method){
    const hints=[];
    for(let i=0;i<method.params.length;i++){
        const paramType=method.params[i]||'';
        if(isCommandParamType(paramType)){
            hints.push({runtimeIndex:i+1,paramType:paramType});
        }
    }
    return hints;
}

function matchHintedArgs(args,hints,isRelevant){
    const matches=[];
    const seen={};
    for(const hint of hints||[]){
        const candidateIndexes=[hint.runtimeIndex];
        if(hint.runtimeIndex>0)candidateIndexes.push(hint.runtimeIndex-1);
        for(const runtimeIndex of candidateIndexes){
            if(runtimeIndex<0)continue;
            const argPtr=args[runtimeIndex];
            if(!argPtr||argPtr.isNull())continue;
            const className=getArgClassName(argPtr);
            if(!className)continue;
            if(!isRelevant(className,hint.paramType))continue;
            const key=runtimeIndex+'|'+className+'|'+argPtr.toString();
            if(seen[key])break;
            seen[key]=true;
            matches.push({index:runtimeIndex,ptr:argPtr,className:className});
            break;
        }
    }
    return matches;
}

function isRelevantSnapshotArg(className,paramType){
    const expected=paramType||'';
    if(expected.includes('NetMessageGameStateSync'))return className.includes('NetMessageGameStateSync');
    if(expected.includes('GameStateSnapshot'))return className.includes('GameStateSnapshot');
    if(expected.includes('NetMessageCombatSim'))return className.includes('NetMessageCombatSim');
    if(expected.includes('NetMessageGameSim'))return className.includes('NetMessageGameSim');
    if(expected.includes('NetMessageRunInitialized'))return className.includes('NetMessageRunInitialized');
    return className.includes('NetMessageGameStateSync')||className.includes('GameStateSnapshot')||className.includes('NetMessageCombatSim')||className.includes('NetMessageGameSim')||className.includes('NetMessageRunInitialized');
}

function getSnapshotMatches(method, args) {
    if (method.snapshotHints && method.snapshotHints.length > 0) {
        // QW10: fast path — trust the hint paramType, skip getArgClassName validation.
        // The hints were built from the method signature at attach time, so the arg
        // at hint.runtimeIndex IS the expected type. Skipping getArgClassName saves
        // 5 NativeFunction calls (2x Process.findRangeByAddress + mono_object_get_class
        // + mono_class_get_namespace + mono_class_get_name) per hint per hook.
        if (FAST_GAMESIM_PATH) {
            const matches = [];
            for (const hint of method.snapshotHints) {
                const argPtr = args[hint.runtimeIndex];
                if (!argPtr || argPtr.isNull()) continue;
                matches.push({index: hint.runtimeIndex, ptr: argPtr, className: hint.paramType});
            }
            return matches;
        }
        return matchHintedArgs(args, method.snapshotHints, isRelevantSnapshotArg);
    }
    return [];
}


function getCommandMatches(method,args){
    if(method.commandHints&&method.commandHints.length>0){
        return matchHintedArgs(args,method.commandHints,(className,paramType)=>isCommandClassName(className)||isCommandParamType(paramType));
    }
    return [];
}

function emitCommandProbe(method, reason, matches, args) {
    try {
        // TODO: Remove this early-return once sell command extraction is resolved.
        // The no-matches path calls inspectArgs which reads class names from
        // process memory on the game thread, causing visible lag during combat
        // when OnAuraEffectExecuted and HandleMessage fire dozens of times/sec.
        if (reason === 'no-matches') return;

        const key = formatMethod(method) + '|' + reason;
        commandProbeLogCounts[key] = (commandProbeLogCounts[key] || 0) + 1;
        if (commandProbeLogCounts[key] > 2) return;

        let detail = '';
        if (matches && matches.length > 0) {
            detail = matches.map(m => 'arg' + m.index + '=' + m.className).join(', ');
        } else {
            const inspected = inspectArgs(method, args);
            detail = inspected.length > 0
                ? inspected.map(m => 'arg' + m.index + '=' + m.className).join(', ')
                : 'no object args';
        }

        send({type:'info', msg:'Command probe ' + reason + ' ' + formatMethod(method) + ' :: ' + detail});
    } catch (e) {}
}

function readEmbeddedSnapshotFromObject(objPtr,classKey){try{const info=fieldInfoCache[classKey];if(!info)return null;for(const field of Object.values(info)){if(!field||!field.type)continue;if(field.type.includes('GameStateSnapshotDTO')){const sp=readObjectField(objPtr,classKey,[field.name]);if(sp&&!sp.isNull())return sp;}}}catch(e){send({type:'debug',msg:'readEmbeddedSnapshotFromObject '+classKey+': '+e});}return null;}

function isSnapshotSearchableType(typeName){if(!typeName)return false;if(typeName==='System.String')return false;if(typeName.startsWith('System.Boolean')||typeName.startsWith('System.Int')||typeName.startsWith('System.UInt')||typeName.startsWith('System.Single')||typeName.startsWith('System.Double')||typeName.startsWith('System.Byte')||typeName.startsWith('System.SByte')||typeName.startsWith('System.Char')||typeName.startsWith('System.Guid'))return false;if(typeName.includes('GameStateSnapshotDTO'))return true;if(typeName.startsWith('BazaarGameShared.Infra.Messages.')||typeName.startsWith('TheBazaar.')||typeName.startsWith('BazaarGameClient.'))return true;return false;}

function findSnapshotInObjectGraph(objPtr,depth,seen){try{if(!objPtr||objPtr.isNull()||depth<0)return null;const ptrKey=objPtr.toString();if(seen.has(ptrKey))return null;seen.add(ptrKey);const klass=mono_object_get_class(objPtr);if(!klass||klass.isNull())return null;const className=classFullName(klass);if(className&&className.includes('GameStateSnapshotDTO'))return{ptr:objPtr,source:className};const fields=getDynamicFieldsForKlass(klass);for(const field of fields){if(!field||!field.type)continue;if(field.type.includes('GameStateSnapshotDTO')){const sp=readObjectFieldByInfo(objPtr,field);if(sp&&!sp.isNull())return{ptr:sp,source:(className||'?')+'.'+field.name};}}if(depth===0)return null;for(const field of fields){if(!isSnapshotSearchableType(field.type))continue;const child=readObjectFieldByInfo(objPtr,field);if(!child||child.isNull())continue;const found=findSnapshotInObjectGraph(child,depth-1,seen);if(found)return found;}return null;}catch(e){return null;}}

function summarizeObjectGraph(objPtr,depth,maxEntries,seen,path,out){try{if(!objPtr||objPtr.isNull()||depth<0||out.length>=maxEntries)return;const ptrKey=objPtr.toString();if(seen.has(ptrKey))return;seen.add(ptrKey);const klass=mono_object_get_class(objPtr);if(!klass||klass.isNull())return;const className=classFullName(klass)||'?';const fields=getDynamicFieldsForKlass(klass);for(const field of fields){if(out.length>=maxEntries)break;if(!field||!field.type||!isSnapshotSearchableType(field.type))continue;const child=readObjectFieldByInfo(objPtr,field);const fieldPath=path?path+'.'+field.name:field.name;if(!child||child.isNull()){out.push(fieldPath+': '+field.type+' = null');continue;}const childKlass=mono_object_get_class(child);const childName=childKlass&&!childKlass.isNull()?classFullName(childKlass):'?';out.push(fieldPath+': '+field.type+' -> '+childName);if(depth>0&&childName&&!childName.startsWith('System.'))summarizeObjectGraph(child,depth-1,maxEntries,seen,fieldPath,out);}}catch(e){}}

function emitObjectGraphSummary(label,objPtr,depth){try{graphSummaryLogCounts[label]=(graphSummaryLogCounts[label]||0)+1;if(graphSummaryLogCounts[label]>1)return;const parts=[];summarizeObjectGraph(objPtr,depth,10,new Set(),'',parts);if(parts.length>0)send({type:'debug',msg:'graph:'+label+' '+parts.join(' | ')});}catch(e){}}

function hasSeenMessageId(messageId){return !!(messageId&&seenMessageIds[messageId]);}

function rememberMessageId(messageId){if(!messageId||seenMessageIds[messageId])return;seenMessageIds[messageId]=true;seenMessageOrder.push(messageId);if(seenMessageOrder.length>MAX_SEEN_MESSAGE_IDS){const expired=seenMessageOrder.shift();if(expired)delete seenMessageIds[expired];}}

function hasSeenCommandKey(commandKey){return !!(commandKey&&seenCommandKeys[commandKey]);}

function rememberCommandKey(commandKey){if(!commandKey||seenCommandKeys[commandKey])return;seenCommandKeys[commandKey]=true;seenCommandOrder.push(commandKey);if(seenCommandOrder.length>MAX_SEEN_COMMAND_KEYS){const expired=seenCommandOrder.shift();if(expired)delete seenCommandKeys[expired];}}

function readCommandEventFromMatch(match){const className=match.className||'';const commandInfo=resolveCommandKindInfo(className);if(!commandInfo)return null;const objPtr=match.ptr;const instanceId=readDynamicStringField(objPtr,['InstanceId','CardInstanceId','EncounterId']);const targetSockets=readDynamicIntListField(objPtr,['TargetSockets','TargetSocketIds','Targets']);const singleSocket=readDynamicI32Field(objPtr,['TargetSocket','Socket']);if(singleSocket!==null&&targetSockets.indexOf(singleSocket)<0)targetSockets.push(singleSocket);const section=readDynamicI32Field(objPtr,['Section']);const commandKey=[commandInfo.commandKey,instanceId||'',targetSockets.join(','),section===null?'':String(section),objPtr.toString()].join('|');if(hasSeenCommandKey(commandKey))return null;rememberCommandKey(commandKey);return{command_id:++commandCounter,event_type:commandInfo.eventType,command_class:commandInfo.simpleName,instance_id:instanceId||null,target_sockets:targetSockets,section:section,hook_source:'arg'+match.index+':'+className,timestamp:Date.now()};} // QW5: Date.now() avoids string alloc + Intl plumbing on game thread

function tryExtractCommandEvent(method,args){const matches=getCommandMatches(method,args);if(matches.length===0){emitCommandProbe(method,'no-matches',matches,args);return null;}let sawCommandLike=false;for(const match of matches){if(!isCommandClassName(match.className))continue;sawCommandLike=true;const event=readCommandEventFromMatch(match);if(event)return event;}emitCommandProbe(method,sawCommandLike?'decode-failed':'non-command-matches',matches,args);return null;}

function tryExtractSnapshot(method,args){
    const matches=getSnapshotMatches(method,args);
    let sawSync=false;
    let sawDataNull=false;
    let sawSnapshotArg=false;
    let sawCombatSim=false;
    let sawGameSim=false;
    let sawRunInitialized=false;
    for(const m of matches){
        let sp=null;
        let source='arg'+m.index+':'+m.className;
        let messageId=null;
        let forceFull=false;
        let allowHeavyCards=true;
        let includePlayerAttrs=true;
        if(m.className.includes('NetMessageGameStateSync')){
            sawSync=true;
            messageId=readMessageIdFromNetMessage(m.ptr,'NetMessageGameStateSync');
            if(hasSeenMessageId(messageId))return{snapshot:null,reason:'duplicate-message',message_id:messageId};
            sp=readObjectField(m.ptr,'NetMessageGameStateSync',['Data','<Data>k__BackingField']);
            forceFull=false;
            if(!sp||sp.isNull()){
                sawDataNull=true;
                logNullField('NetMessageGameStateSync.Data','was null from '+m.className+' via arg'+m.index);
            }
        }else if(m.className.includes('GameStateSnapshot')){
            sawSnapshotArg=true;
            sp=m.ptr;
            forceFull=false;
        }else if(m.className.includes('NetMessageCombatSim')){
            sawCombatSim=true;
            allowHeavyCards=FULL_DELTA_CARDS;
            includePlayerAttrs=DELTA_PLAYER_ATTRS;
            messageId=FAST_GAMESIM_PATH?_fastReadMessageId(m.ptr,'NetMessageCombatSim'):readMessageIdFromNetMessage(m.ptr,'NetMessageCombatSim');
            if(hasSeenMessageId(messageId))return{snapshot:null,reason:'duplicate-message',message_id:messageId};
            const dataPtr=FAST_GAMESIM_PATH?_fastReadDataField(m.ptr,'NetMessageCombatSim'):readObjectField(m.ptr,'NetMessageCombatSim',['Data','<Data>k__BackingField']);
            if(FAST_GAMESIM_PATH){
                const dynSnap=readGameSimFast(dataPtr,allowHeavyCards,includePlayerAttrs,true);
                if(dynSnap){
                    if(messageId)dynSnap.message_id=messageId;
                    maybeReportAttrsStats();
                    return{snapshot:dynSnap,source:source+' -> dynamic-data(fast-combatsim)',reason:'snapshot',message_id:messageId};
                }
            }else{
            let dynSnap=readDynamicStateLean(dataPtr);
            const wantCombatCards=dynSnap&&(allowHeavyCards?shouldReadHeavyCards(dynSnap,false):shouldReadActionCards(dynSnap));
            const wantCombatTemplateEvents=dynSnap&&shouldReadActionTemplateEvents(dynSnap);
            if(includePlayerAttrs||wantCombatCards||wantCombatTemplateEvents){
                const richerDynSnap=readDynamicStatePayload(dataPtr,wantCombatCards,includePlayerAttrs,wantCombatTemplateEvents,dynSnap);
                if(richerDynSnap)dynSnap=richerDynSnap;
            }
            if(dynSnap){
                if(messageId)dynSnap.message_id=messageId;
                return{snapshot:dynSnap,source:source+' -> dynamic-data',reason:'snapshot',message_id:messageId};
            }
            }
            // Hot path: do not crawl the object graph on CombatSim misses.
            // The lean dynamic payload is enough for correlation/state, and the
            // fallback graph walk was a major source of game-thread hitching.
        }else if(m.className.includes('NetMessageGameSim')){
            sawGameSim=true;
            allowHeavyCards=FULL_DELTA_CARDS;
            includePlayerAttrs=DELTA_PLAYER_ATTRS;
            messageId=FAST_GAMESIM_PATH?_fastReadMessageId(m.ptr,'NetMessageGameSim'):readMessageIdFromNetMessage(m.ptr,'NetMessageGameSim');
            if(hasSeenMessageId(messageId))return{snapshot:null,reason:'duplicate-message',message_id:messageId};
            const dataPtr=FAST_GAMESIM_PATH?_fastReadDataField(m.ptr,'NetMessageGameSim'):readObjectField(m.ptr,'NetMessageGameSim',['Data','<Data>k__BackingField']);
            // QW9: Fast single-pass path (replaces lean+payload double-read)
            if(FAST_GAMESIM_PATH){
                // Single read with all needed flags — readGameSimFast handles
                // attrs throttling internally, so we always pass includePlayerAttrs
                // and let the function decide whether to actually read them.
                const wantCardsEager=allowHeavyCards;// will be filtered by state inside
                const dynSnap=readGameSimFast(dataPtr,wantCardsEager,includePlayerAttrs,true);
                if(dynSnap){
                    if(messageId)dynSnap.message_id=messageId;
                    maybeReportAttrsStats();
                    return{snapshot:dynSnap,source:source+' -> dynamic-data(fast-gamesim)',reason:'snapshot',message_id:messageId};
                }
            }else{
            // Legacy double-read path
            let dynSnap=readDynamicStateLean(dataPtr);
            const wantGameCards=dynSnap&&(allowHeavyCards?shouldReadHeavyCards(dynSnap,false):shouldReadActionCards(dynSnap));
            const wantGameTemplateEvents=dynSnap&&shouldReadActionTemplateEvents(dynSnap);
            if(includePlayerAttrs||wantGameCards||wantGameTemplateEvents){
                const richerDynSnap=readDynamicStatePayload(dataPtr,wantGameCards,includePlayerAttrs,wantGameTemplateEvents,dynSnap);
                if(richerDynSnap)dynSnap=richerDynSnap;
            }
            if(dynSnap){
                if(messageId)dynSnap.message_id=messageId;
                return{snapshot:dynSnap,source:source+' -> dynamic-data',reason:'snapshot',message_id:messageId};
            }
            }
            // Hot path: do not crawl the object graph on GameSim misses.
            // Keep the lean dynamic snapshot and skip expensive fallback probing.
        }else if(m.className.includes('NetMessageRunInitialized')){
            sawRunInitialized=true;
            messageId=readMessageIdFromNetMessage(m.ptr,'NetMessageRunInitialized');
            if(hasSeenMessageId(messageId))return{snapshot:null,reason:'duplicate-message',message_id:messageId};
            const dataPtr=readObjectField(m.ptr,'NetMessageRunInitialized',['Data','<Data>k__BackingField']);
            let dynSnap=readDynamicStatePayload(dataPtr,false,true,false);
            if(dynSnap&&shouldAllowInlineCardRead(dynSnap,true)){
                const fullDynSnap=readDynamicStatePayload(dataPtr,true,true,false);
                if(fullDynSnap)dynSnap=fullDynSnap;
            }
            if(dynSnap){
                if(messageId)dynSnap.message_id=messageId;
                return{snapshot:dynSnap,source:source+' -> dynamic-data',reason:'snapshot',message_id:messageId};
            }
            const found=readEmbeddedSnapshotFromObject(m.ptr,'NetMessageRunInitialized');
            if(found){
                sp=found.ptr||found;
                source+=' -> '+(found.source||'embedded');
                forceFull=true;
            }
        }else if(m.className.endsWith('GameSimHandler')||m.className.endsWith('CombatSimHandler')||m.className.endsWith('RunInitializedHandler')){
            allowHeavyCards=FULL_DELTA_CARDS;
            // Hot path: skip handler object-graph crawling. These handlers are
            // useful hook surfaces, but walking their graphs on every message is too expensive.
        }
        if(sp&&!sp.isNull()){
            let snap=readGameStateSnapshot(sp,false);
            const wantSnapshotCards=snap&&(allowHeavyCards?shouldReadHeavyCards(snap,forceFull):shouldReadActionCards(snap));
            if(wantSnapshotCards){
                const fullSnap=readGameStateSnapshot(sp,true);
                if(fullSnap)snap=fullSnap;
            }
            if(snap){
                if(messageId)snap.message_id=messageId;
                return{snapshot:snap,source:source,reason:'snapshot',message_id:messageId};
            }
        }
    }
    if(sawDataNull)return{snapshot:null,reason:'data-null'};
    if(sawSync)return{snapshot:null,reason:'sync-without-snapshot'};
    if(sawSnapshotArg)return{snapshot:null,reason:'snapshot-arg-read-failed'};
    if(sawCombatSim)return{snapshot:null,reason:'combat-sim'};
    if(sawGameSim)return{snapshot:null,reason:'game-sim'};
    if(sawRunInitialized)return{snapshot:null,reason:'run-initialized'};
    if(matches.length===0)return{snapshot:null,reason:'no-object-args'};
    return{snapshot:null,reason:'no-matching-arg'};
}

function hookMethod(method){const c=mono_compile_method(method.ptr);if(!c||c.isNull()){send({type:'error',msg:'JIT fail: '+formatMethod(method)});return false;}const codeKey=c.toString();if(hookedCode[codeKey])return hookedCode[codeKey]==='capture';hookedCode[codeKey]='capture';send({type:'info',msg:'Hooking '+formatMethod(method)+' at '+c});Interceptor.attach(c,{onEnter:function(args){const t0=Date.now();let t1=t0;try{resetRangeCache();const methodKey=formatMethod(method);captureCallCounts[methodKey]=(captureCallCounts[methodKey]||0)+1;const callCount=captureCallCounts[methodKey];// QW8: skip tryExtractCommandEvent on methods with no command hints (saves ~0.1ms per call)
const commandEvent=(method.captureCommands&&method.commandHints&&method.commandHints.length>0)?tryExtractCommandEvent(method,args):null;if(method.commandOnly){t1=Date.now();if(commandEvent){commandEvent.t_hook=t0;commandEvent.hook_duration=t1-t0;commandEvent.hook_method=method.name;send({type:'command_event',data:commandEvent});}if((t1-t0)>=SLOW_HOOK_MS){send({type:'perf',stage:'hook',hook:methodKey,hook_duration:t1-t0,call_count:callCount,status:'command-only'});}return;}const hit=tryExtractSnapshot(method,args);t1=Date.now();let status=hit&&hit.reason?hit.reason:'no-result';if(commandEvent&&status==='no-result')status='command';if(VERBOSE_HOOK_CALLS&&(callCount<=5||callCount%10===0)){send({type:'capture_call',method:methodKey,count:callCount,status:status,hook_duration:t1-t0});}if((t1-t0)>=SLOW_HOOK_MS){send({type:'perf',stage:'hook',hook:methodKey,hook_duration:t1-t0,call_count:callCount,status:status});}const snap=hit&&hit.snapshot?hit.snapshot:null;if(commandEvent){commandEvent.t_hook=t0;commandEvent.hook_duration=t1-t0;commandEvent.hook_method=method.name;}if(!snap&&!commandEvent)return;if(snap){const messageId=hit&&hit.message_id?hit.message_id:snap.message_id;if(messageId)rememberMessageId(messageId);snapshotCounter++;snap.id=snapshotCounter;snap.hook=method.name;snap.hook_source=hit&&hit.source?hit.source:null;snap.timestamp=Date.now();snap.t_hook=t0;snap.hook_duration=t1-t0;snap.hook_method=method.name;}if(commandEvent&&snap){send({type:'batch',items:[{type:'command_event',data:commandEvent},{type:'game_state',data:snap}]});}else if(snap){send({type:'game_state',data:snap});}else if(commandEvent){send({type:'command_event',data:commandEvent});}}catch(e){t1=Date.now();send({type:'error',msg:method.name+': '+e+' (hook_duration='+(t1-t0)+'ms)'});}}});return true;}

function attachProbe(method,prefix){if(!ENABLE_PROBES)return false;try{const c=mono_compile_method(method.ptr);if(!c||c.isNull())return false;const codeKey=c.toString();if(hookedCode[codeKey])return hookedCode[codeKey]==='probe';hookedCode[codeKey]='probe';Interceptor.attach(c,{onEnter:function(args){const key=prefix+'.'+method.name+'/'+method.paramCount;probeLogCounts[key]=(probeLogCounts[key]||0)+1;if(probeLogCounts[key]<=4)send({type:'probe',msg:prefix+'.'+formatMethod(method)+' fired (#'+probeLogCounts[key]+')',method:key});}});send({type:'debug',msg:'Probe: '+prefix+'.'+formatMethod(method)});return true;}catch(e){return false;}}

function parseTypeName(typeName){if(!typeName)return null;let t=typeName.trim();if(t.startsWith('class '))t=t.slice(6);if(t.startsWith('valuetype '))t=t.slice(10);const comma=t.indexOf(',');if(comma>=0)t=t.slice(0,comma);const lt=t.indexOf('<');if(lt>=0)t=t.slice(0,lt);const lastDot=t.lastIndexOf('.');if(lastDot<0)return{ns:'',cls:t};return{ns:t.slice(0,lastDot),cls:t.slice(lastDot+1)};}

function hookDataUpdater(handlerKlass){const fields=getFields(handlerKlass);const dataField=fields.find(f=>f.name==='Data'||f.name==='<Data>k__BackingField');if(!dataField||!dataField.type){send({type:'debug',msg:'GameStateHandler Data field not found or has no type info'});return 0;}send({type:'info',msg:'GameStateHandler Data field type: '+dataField.type});const parsed=parseTypeName(dataField.type);if(!parsed)return 0;const dataKlass=findClass(parsed.ns,parsed.cls);if(!dataKlass){send({type:'debug',msg:'Could not resolve data class '+parsed.ns+'.'+parsed.cls});return 0;}const methods=getMethods(dataKlass);send({type:'info',msg:parsed.cls+' methods ('+methods.length+'):'});for(const m of methods)send({type:'debug',msg:'  '+formatMethod(m)});let hooked=0;for(const m of methods){if(m.name.includes('UpdateFromStateSync')||m.name.includes('HandleStateSync')||m.name.includes('ApplyState')||m.name.includes('SyncState')){if(hookMethod(m))hooked++;}}if(ENABLE_PROBES){const skip=['ToString','GetHashCode','Equals','Finalize','MemberwiseClone','GetType','.ctor','.cctor'];for(const m of methods){if(skip.includes(m.name)||m.name.startsWith('get_')||m.name.startsWith('set_'))continue;attachProbe(m,parsed.cls);}}return hooked;}

function hookAllCandidates(klass){const methods=getMethods(klass);const cands=['UpdateFromStateSync','HandleStateSync','OnStateSync','HandleMessage','OnMessage','ProcessMessage','UpdateFromState','OnGameState','HandleGameState','UpdateState','SyncState','ApplyState'];let hooked=0;for(const m of methods){if(cands.some(c=>m.name.includes(c))){if(hookMethod(m))hooked++;}}if(ENABLE_PROBES){const skip=['ToString','GetHashCode','Equals','Finalize','MemberwiseClone','GetType','.ctor','.cctor'];let probes=0;for(const m of methods){if(skip.includes(m.name)||m.name.startsWith('get_')||m.name.startsWith('set_'))continue;if(attachProbe(m,'GameStateHandler'))probes++;}send({type:'info',msg:'Attached '+probes+' passive GameStateHandler probe(s).'});}return hooked;}

function methodHasRelevantParam(method){for(const p of method.params){if(p.includes('NetMessageGameStateSync')||p.includes('GameStateSnapshotDTO')||p.includes('NetMessageCombatSim')||p.includes('NetMessageGameSim')||p.includes('NetMessageRunInitialized'))return true;}return false;}

function methodHasCommandParam(method){for(const p of method.params){if(isCommandParamType(p))return true;}return false;}

function isRelevantGlobalClass(fullName){const exact=['TheBazaar.Data','TheBazaar.NetMessageProcessor','TheBazaar.AppState','TheBazaar.StartRunAppState','TheBazaar.GameStateHandler','TheBazaar.CombatSimHandler','TheBazaar.GameSimHandler','TheBazaar.RunInitializedHandler'];return exact.includes(fullName);}

function isRelevantGlobalMethod(cls,method){
    if(method.name.startsWith('add_')||method.name.startsWith('remove_')||method.name.startsWith('<'))return false;
    if(method.name==='CanProcessMessages')return false;
    if(!methodHasRelevantParam(method))return false;
    const className=cls.fullName;
    if(!ENABLE_BROAD_HOOKS){
        // Keep the default hook set lean, but include the alternate router/state-sync
        // entrypoints that broad mode already trusts. Mak run 339 showed the
        // previous 4-method set can go completely silent while API state traffic
        // still exists, so we need a little more coverage by default.
        if(className==='TheBazaar.NetMessageProcessor'&&method.name==='Handle')return true;
        if(className==='TheBazaar.AppState'&&(method.name==='OnGameStateSyncMessageReceived'||method.name==='OnStateSyncMessage'))return true;
        if(className==='TheBazaar.StartRunAppState'&&method.name==='OnStateSyncMessage')return true;
        if(className==='TheBazaar.GameSimHandler'&&method.name==='HandleMessage')return true;
        if(className==='TheBazaar.CombatSimHandler'&&method.name==='HandleMessage')return true;
        if(className==='TheBazaar.RunInitializedHandler'&&method.name==='HandleMessage')return true;
        return false;
    }
    if(className==='TheBazaar.Data'&&method.name==='UpdateFromStateSync')return true;
    if(className==='TheBazaar.NetMessageProcessor'&&method.name==='Handle')return true;
    if(className==='TheBazaar.AppState'&&(method.name==='OnGameStateSyncMessageReceived'||method.name==='OnStateSyncMessage'))return true;
    if(className==='TheBazaar.StartRunAppState'&&method.name==='OnStateSyncMessage')return true;
    if(className==='TheBazaar.GameStateHandler'&&method.name==='HandleMessage')return true;
    if(className==='TheBazaar.CombatSimHandler'&&method.name==='HandleMessage')return true;
    if(className==='TheBazaar.GameSimHandler'&&method.name==='HandleMessage')return true;
    if(className==='TheBazaar.RunInitializedHandler'&&method.name==='HandleMessage')return true;
    return false;
}

function isRelevantCommandMethod(cls,method){if(method.name.startsWith('add_')||method.name.startsWith('remove_')||method.name.startsWith('<'))return false;if(method.name.startsWith('get_')||method.name.startsWith('set_'))return false;if(method.name==='CanProcessMessages')return false;const className=cls.fullName||'';const hasCommandParam=methodHasCommandParam(method);const commandishName=method.name.includes('Send')||method.name.includes('Handle')||method.name.includes('Execute')||method.name.includes('Process')||method.name.includes('Dispatch')||method.name.includes('Queue');const commandishClass=className.includes('Command')||className.includes('Network')||className.includes('Client')||className.includes('Handler')||className.includes('State')||className.includes('Controller');if(hasCommandParam)return true;if(commandishName&&commandishClass)return true;return false;}

function hookGlobalSearchCandidates(){const assemblies=['TheBazaarRuntime','BazaarGameClient','Assembly-CSharp'];let classCount=0;let methodCount=0;let hooked=0;for(const assemblyName of assemblies){const image=imageMap[assemblyName];if(!image)continue;const classes=enumerateClassesInImage(image,assemblyName);send({type:'info',msg:'Scanning '+classes.length+' classes in '+assemblyName+' for additional state-sync hooks...'});for(const cls of classes){classCount++;if(!isRelevantGlobalClass(cls.fullName))continue;let methods=[];try{methods=getMethods(cls.klass);}catch(e){continue;}const hits=methods.filter(m=>isRelevantGlobalMethod(cls,m));if(hits.length===0)continue;send({type:'debug',msg:'Global candidate '+cls.fullName+' in '+assemblyName+': '+hits.map(formatMethod).join(' | ')});for(const method of hits){// SCOPED OUT: captureCommands disabled in W/L-only mode
// Re-enabled from W/L-only scope-down: captureCommands and commandHints restored
const configured=cloneMethodWithMeta(method,{ownerClass:cls.fullName,snapshotHints:buildSnapshotHints(method),commandHints:buildCommandHints(method),captureCommands:true,commandOnly:false});methodCount++;if(hookMethod(configured))hooked++;}}}send({type:'info',msg:'Global scan checked '+classCount+' classes and '+methodCount+' focused candidate method(s); hooked '+hooked+'.'});return hooked;}

// Re-enabled from W/L-only scope-down (F7+F9): command hooks with fixed allow-list
const COMMAND_HOOK_ALLOWLIST = {
    SelectItemCommand: true,
    SelectSkillCommand: true,
    SellCardCommand: true,
    RerollCommand: true,
    SelectEncounterCommand: true,
    CommitToPedestalCommand: true,
    ExitCurrentStateCommand: true,
};
function hookCommandSearchCandidates(){const assemblies=['TheBazaarRuntime','BazaarGameClient','Assembly-CSharp'];let classCount=0;let methodCount=0;let hooked=0;for(const assemblyName of assemblies){const image=imageMap[assemblyName];if(!image)continue;const classes=enumerateClassesInImage(image,assemblyName);send({type:'info',msg:'Scanning '+classes.length+' classes in '+assemblyName+' for command hooks...'});for(const cls of classes){classCount++;let methods=[];try{methods=getMethods(cls.klass);}catch(e){continue;}const hits=methods.filter(m=>isRelevantCommandMethod(cls,m));if(hits.length===0)continue;// F7: filter to allowed command types only
const allowedHits=hits.filter(m=>{for(const p of m.params){const simple=(p.split('.').pop()||'').split('`')[0];if(COMMAND_HOOK_ALLOWLIST[simple])return true;}return false;});if(allowedHits.length===0)continue;send({type:'debug',msg:'Command candidate '+cls.fullName+' in '+assemblyName+': '+allowedHits.map(formatMethod).join(' | ')});for(const method of allowedHits){const configured=cloneMethodWithMeta(method,{ownerClass:cls.fullName,commandHints:buildCommandHints(method),captureCommands:true,commandOnly:true});methodCount++;if(hookMethod(configured))hooked++;}}}send({type:'info',msg:'Command scan checked '+classCount+' classes and '+methodCount+' candidate method(s); hooked '+hooked+'.'});return hooked;}

// QW1: Pre-warm DTO field caches at attach time to eliminate first-encounter 50-100ms spikes.
// Called before hooks are installed so the hook thread never hits a cold class walk.
function prewarmFieldInfoCache(){
    // Known DTO types from searchTargets and message types
    const prewarmTypes=[
        {ns:'BazaarGameShared.Infra.Messages',cls:'GameStateSnapshotDTO'},
        {ns:'BazaarGameShared.Infra.Messages',cls:'RunSnapshotDTO'},
        {ns:'BazaarGameShared.Infra.Messages',cls:'PlayerSnapshotDTO'},
        {ns:'BazaarGameShared.Infra.Messages',cls:'RunStateSnapshotDTO'},
        {ns:'BazaarGameShared.Infra.Messages',cls:'NetMessageGameStateSync'},
        {ns:'BazaarGameShared.Infra.Messages',cls:'NetMessageCombatSim'},
        {ns:'BazaarGameShared.Infra.Messages',cls:'NetMessageGameSim'},
        {ns:'BazaarGameShared.Infra.Messages',cls:'NetMessageRunInitialized'},
        {ns:'BazaarGameShared.Infra.Messages',cls:'CardSnapshotDTO'},
        {ns:'BazaarGameShared.Infra.Messages.GameSimEvents',cls:'GameSim'},
        {ns:'BazaarGameShared.Infra.Messages.GameSimEvents',cls:'SimUpdateRun'},
        {ns:'BazaarGameShared.Infra.Messages.GameSimEvents',cls:'SimUpdateRunState'},
        {ns:'BazaarGameShared.Infra.Messages.GameSimEvents',cls:'SimUpdatePlayer'},
        {ns:'BazaarGameShared.Infra.Messages.GameSimEvents',cls:'SimUpdateCard'},
        {ns:'BazaarGameShared.Infra.Messages.GameSimEvents',cls:'CardDeltaPlacement'},
        {ns:'BazaarGameShared.Infra.Messages.GameSimEvents',cls:'GameSimEventCardDealt'},
        {ns:'BazaarGameShared.Infra.Messages.GameSimEvents',cls:'GameSimEventCardSpawned'},
    ];
    let warmed=0;
    for(const t of prewarmTypes){
        try{
            const fullName=(t.ns?t.ns+'.':'')+t.cls;
            if(fieldInfoCache[fullName]){warmed++;continue;}
            // Check if already cached under short key from searchTargets loop
            if(fieldInfoCache[t.cls]){
                // Re-register under fullName key so getFieldInfoForTypeName finds it
                fieldCache[fullName]=fieldCache[t.cls];
                fieldInfoCache[fullName]=fieldInfoCache[t.cls];
                warmed++;continue;
            }
            // Otherwise do a cold walk now (at attach time, not on hook thread)
            const klass=foundClasses[t.cls]?foundClasses[t.cls].klass:findClass(t.ns,t.cls);
            if(!klass||klass.isNull())continue;
            const fields=getFields(klass);
            const map={};const info={};
            for(const f of fields){map[f.name]=f.offset;info[f.name]=f;}
            fieldCache[fullName]=map;fieldInfoCache[fullName]=info;
            dynamicFieldInfoCache[fullName]=fields;
            warmed++;
        }catch(e){send({type:'debug',msg:'prewarm '+t.cls+': '+e});}
    }
    send({type:'info',msg:'QW1: pre-warmed '+warmed+'/'+prewarmTypes.length+' DTO field caches.'});
}

// Execute
const __captureMonoInitialized=(function(){
    // QW1: pre-warm before hooks fire to eliminate cold-walk spikes
    prewarmFieldInfoCache();
    _fieldInfoPrewarmed=true;
    const gh=hookGlobalSearchCandidates();const ch=hookCommandSearchCandidates();if(ENABLE_BROAD_HOOKS&&foundClasses['GameStateHandler']){const handlerKlass=foundClasses['GameStateHandler'].klass;const h=hookAllCandidates(handlerKlass);const dh=hookDataUpdater(handlerKlass);const total=h+dh+gh+ch;if(total>0)send({type:'ready',msg:'Mono hooks active. '+total+' capture method(s) hooked.'});else send({type:'info',msg:'Probes attached - play to identify methods.'});}else if(gh+ch>0){send({type:'ready',msg:'Mono hooks active. '+(gh+ch)+' capture method(s) hooked.'});}else if(foundClasses['GameStateHandler']){send({type:'info',msg:'Searching broader namespaces...'});const nsG=['TheBazaar','TheBazaar.Runtime','TheBazaar.Game','TheBazaar.Infra','TheBazaar.Network','TheBazaar.State','Bazaar','Game','','Runtime'];let found=false;for(const[an,img]of Object.entries(imageMap)){if(!['TheBazaarRuntime','Assembly-CSharp','BazaarGameClient'].includes(an))continue;for(const ns of nsG){const k=mono_class_from_name(img,Memory.allocUtf8String(ns),Memory.allocUtf8String('GameStateHandler'));if(!k.isNull()){send({type:'info',msg:'FOUND at ns="'+ns+'" in '+an});foundClasses['GameStateHandler']={klass:k,ns};if(ENABLE_BROAD_HOOKS){const h=hookAllCandidates(k);const dh=hookDataUpdater(k);const gh2=hookGlobalSearchCandidates();const ch2=hookCommandSearchCandidates();if(h+dh+gh2+ch2>0)send({type:'ready',msg:'Mono hooks active. '+(h+dh+gh2+ch2)+' capture method(s) hooked.'});}found=true;break;}}if(found)break;}if(!found)send({type:'error',msg:'GameStateHandler not found. Assemblies: '+Object.keys(imageMap).join(', ')});}else{send({type:'error',msg:'No preferred capture hooks resolved. Assemblies: '+Object.keys(imageMap).join(', ')});}return true;})();
if(false&&foundClasses['GameStateHandler']){const h=hookAllCandidates(foundClasses['GameStateHandler'].klass);if(h>0)send({type:'ready',msg:'Mono hooks active. '+h+' method(s) hooked.'});else send({type:'info',msg:'Probes attached - play to identify methods.'});}else if(false){send({type:'info',msg:'Searching broader namespaces...'});const nsG=['TheBazaar','TheBazaar.Runtime','TheBazaar.Game','TheBazaar.Infra','TheBazaar.Network','TheBazaar.State','Bazaar','Game','','Runtime'];let found=false;for(const[an,img]of Object.entries(imageMap)){if(!['TheBazaarRuntime','Assembly-CSharp','BazaarGameClient'].includes(an))continue;for(const ns of nsG){const k=mono_class_from_name(img,Memory.allocUtf8String(ns),Memory.allocUtf8String('GameStateHandler'));if(!k.isNull()){send({type:'info',msg:'FOUND at ns="'+ns+'" in '+an});foundClasses['GameStateHandler']={klass:k,ns};hookAllCandidates(k);found=true;break;}}if(found)break;}if(!found)send({type:'error',msg:'GameStateHandler not found. Assemblies: '+Object.keys(imageMap).join(', ')});}
""";


_output_dir = None
_do_log = False
_do_db = False
_log_file = None
_snapshot_count = 0
_probe_hits = {}
_capture_calls = {}
_seen_snapshot_keys = set()
_duplicate_snapshot_count = 0
_last_merged_snapshot = None
_pending_snapshot_db_by_key: dict[str, dict] = {}
_pending_snapshot_db_keys: set[str] = set()
_coalesced_snapshot_db_updates = 0
_db_queue = None
_db_thread = None
_api_log_module = None
_CARD_LIST_KEYS = (
    "offered",
    "player_board",
    "player_stash",
    "player_skills",
    "opponent_board",
)
_PERSISTENT_CARD_KEYS = (
    "player_board",
    "player_stash",
    "player_skills",
    "opponent_board",
)
_VERBOSE_DEBUG = False
_VERBOSE_HOOKS = False
_RENDER_ALL_SNAPSHOTS = False
_DETAILED_SNAPSHOTS = False
_FULL_DELTA_CARDS = False
# Snapshot printing is compact-by-default (one line) to keep the console
# cheap during bursty GameSim deltas. Use --verbose-snapshots for the
# legacy multi-line block. _SNAPSHOT_PRINT_MIN_INTERVAL_MS rate-limits
# the verbose path so a burst of identical snapshots only emits one
# block per interval.
_COMPACT_SNAPSHOTS = True
_SNAPSHOT_PRINT_MIN_INTERVAL_MS = 500.0
_last_snapshot_print_ms: float = 0.0
_snapshot_prints_suppressed: int = 0
# Re-enabled from W/L-only scope-down
_DELTA_PLAYER_ATTRS = True
# Leave action-time card decoding off by default. It is useful for debugging
# inferred move/buy/sell coverage, but it adds extra GameSim work during the
# exact click paths where hitching is most noticeable (sell / event choice).
_ACTION_EVENT_CARDS = False
# Opponent board is not used by the live overlay path, so keep it out of the
# default deferred snapshot payload unless explicitly requested for debugging.
_CAPTURE_OPPONENT_BOARD = False
# ENABLE_PROBES kept disabled (noisy, not needed)
_ENABLE_PROBES = False
# ENABLE_BROAD_HOOKS kept disabled (narrow hooks sufficient)
_ENABLE_BROAD_HOOKS = False
_rendered_snapshot_keys = set()
_last_action_snapshot = None
_action_event_seq = 0
_pending_direct_rerolls = 0
_mono_db_conn = None
_event_template_ids_by_instance: dict = {}
# F1: deferred card data keyed by snapshot_id â€” merged into snapshots when the deferred message arrives
_deferred_cards_by_snapshot_id: dict = {}
_deferred_template_events_by_snapshot_id: dict = {}
# Deferred Player.Attributes (mirrors deferred_cards): JS agent enumerates the
# managed Attributes dict off the game thread and ships the decoded key-value
# map here. Stored by snapshot_id so _merge_partial_snapshot can pick it up if
# it arrived before the snapshot; also merged in-place into _last_merged_snapshot
# on late arrival so the persisted row reflects the attrs.
_deferred_attrs_by_snapshot_id: dict = {}
_deferred_attrs_pickup_count = 0       # applied via _merge_partial_snapshot pickup (attrs arrived first)
_deferred_attrs_late_arrival_count = 0  # merged into _last_merged_snapshot after it was persisted
_deferred_attrs_dropped_count = 0      # dropped due to cap eviction or empty payload
_deferred_attrs_last_stat_log_ms = 0.0
_DEFERRED_ATTRS_STAT_INTERVAL_MS = 60000.0
_SLOW_HOOK_LOG_THRESHOLD_MS = 8.0


def _get_mono_conn():
    """Return a reusable SQLite connection for the mono-db-writer thread."""
    global _mono_db_conn
    if _mono_db_conn is None:
        import sqlite3
        _mono_db_conn = sqlite3.connect(
            Path(__file__).parent / "bazaar_runs.db",
            timeout=30.0,
        )
        _mono_db_conn.row_factory = sqlite3.Row
        _mono_db_conn.execute("PRAGMA journal_mode=WAL")
        _mono_db_conn.execute("PRAGMA synchronous=NORMAL")
        _mono_db_conn.execute("PRAGMA foreign_keys=ON")
        _mono_db_conn.execute("PRAGMA busy_timeout=30000")
    return _mono_db_conn


_INTERESTING_RENDER_STATES = {
    "Choice",
    "Loot",
    "LevelUp",
    "Pedestal",
    "EndRunVictory",
    "EndRunDefeat",
}

_INFO_SUPPRESS_PREFIXES = (
    "Found ",
    "Images:",
    "GameStateHandler methods",
    "GameStateHandler fields",
    "Hooking ",
    "Attached ",
    "Scanning ",
    "Global scan checked ",
)

_DEBUG_ALLOW_SUBSTRINGS = (
    "was null",
    "readObjectField",
    "readSnapshot:",
    "readPlayer:",
    "readCard:",
    "readState:",
    "readRun:",
    "HashSet _slots not found",
    # Temporary decode-investigation logs; re-enable when tracing DTO layouts.
    # "graph:",
    # "PlayerSnapshotDTO.Attributes",
    # "DynamicPlayer.Attributes",
    # "enum-int dict ",
)


def _should_print_info(msg: str) -> bool:
    return not any(msg.startswith(prefix) for prefix in _INFO_SUPPRESS_PREFIXES)


def _should_print_debug(msg: str) -> bool:
    return _VERBOSE_DEBUG and any(token in msg for token in _DEBUG_ALLOW_SUBSTRINGS)


def _should_render_snapshot(gs: dict) -> bool:
    if _RENDER_ALL_SNAPSHOTS:
        return True
    if _snapshot_count == 0:
        return True

    state = gs.get("state", {})
    state_name = state.get("state")
    return state_name in _INTERESTING_RENDER_STATES


def _render_signature(gs: dict) -> str:
    # Re-enabled from W/L-only scope-down: selection_set and card counts restored to signature
    run = gs.get("run", {})
    state = gs.get("state", {})
    player = gs.get("player", {})
    return json.dumps(
        {
            "state": state.get("state"),
            "day": run.get("day"),
            "hour": run.get("hour"),
            "gold": player.get("Gold"),
            "hp": player.get("Health"),
            "hp_max": player.get("HealthMax"),
            "prestige": player.get("Prestige"),
            "wins": run.get("victories"),
            "losses": run.get("defeats"),
            "selection_set": _normalized_selection(state.get("selection_set")),
            "offered_count": len(gs.get("offered", [])),
            "board_count": len(gs.get("player_board", [])),
            "skills_count": len(gs.get("player_skills", [])),
            "opponent_count": len(gs.get("opponent_board", [])),
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _normalized_selection(values):
    if not values:
        return []
    normalized = []
    for value in values:
        if value in (None, "", "None"):
            continue
        if isinstance(value, str):
            candidate = value.strip()
            if not candidate:
                continue
            has_ascii_identifier = any(ch.isascii() and ch.isalnum() for ch in candidate)
            has_non_ascii = any(not ch.isascii() for ch in candidate)
            if has_non_ascii and not has_ascii_identifier:
                continue
            normalized.append(candidate)
        else:
            normalized.append(value)
    return normalized


def _perf_now_ms() -> float:
    return time.perf_counter() * 1000.0


def _log_hook_perf(payload: dict):
    if payload.get("stage") != "hook":
        return
    try:
        duration_ms = float(payload.get("hook_duration"))
    except Exception:
        return
    if duration_ms < _SLOW_HOOK_LOG_THRESHOLD_MS:
        return
    fields = {
        "hook": payload.get("hook"),
        "call_count": payload.get("call_count"),
        "status": payload.get("status"),
    }
    detail_text = " ".join(
        f"{key}={value}" for key, value in fields.items() if value not in (None, "", [], {})
    )
    suffix = f" | {detail_text}" if detail_text else ""
    print(f"[MonoPerf] slow hook: {duration_ms:.1f} ms{suffix}")


# Re-enabled from W/L-only scope-down (F2)
def _clone_snapshot_for_actions(gs: dict) -> dict:
    cloned = {
        "run": dict(gs.get("run", {})),
        "state": dict(gs.get("state", {})),
        "player": dict(gs.get("player", {})),
    }
    for key in _CARD_LIST_KEYS:
        cloned[key] = [dict(card) for card in gs.get(key, [])]
    return cloned


def _prune_disabled_snapshot_cards(gs: dict) -> dict:
    """Drop snapshot sections that are intentionally disabled in this run."""
    if not _CAPTURE_OPPONENT_BOARD:
        gs["opponent_board"] = []
    return gs


# Re-enabled from W/L-only scope-down (F2)
def _card_map(gs: dict) -> dict:
    cards_by_id = {}
    for category in _CARD_LIST_KEYS:
        for card in gs.get(category, []) or []:
            instance_id = card.get("instance_id")
            if not instance_id:
                continue
            cards_by_id[instance_id] = {
                "instance_id": instance_id,
                "template_id": card.get("template_id"),
                "category": category,
                "socket": card.get("socket"),
                "type": card.get("type"),
                "tier": card.get("tier"),
                "section": card.get("section"),
            }
    return cards_by_id


# Re-enabled from W/L-only scope-down (F2)
def _numeric_delta(before, after):
    if isinstance(before, (int, float)) and isinstance(after, (int, float)):
        return after - before
    return None


def _is_action_state(state_name: str | None) -> bool:
    return state_name in {
        "Choice",
        "Encounter",
        "Loot",
        "LevelUp",
        "Pedestal",
        "EndRunVictory",
        "EndRunDefeat",
    }


def _make_action_event(gs: dict, event_type: str, **details) -> dict:
    global _action_event_seq

    _action_event_seq += 1
    run = gs.get("run", {})
    state = gs.get("state", {})
    player = gs.get("player", {})
    return {
        "event_seq": _action_event_seq,
        "captured_at": gs.get("timestamp", datetime.datetime.now().isoformat()),
        "snapshot_id": gs.get("id"),
        "message_id": gs.get("message_id"),
        "event_type": event_type,
        "run_state": state.get("state"),
        "hero": player.get("hero"),
        "day": run.get("day"),
        "hour": run.get("hour"),
        "gold": player.get("Gold"),
        "details": details,
    }


def _infer_action_events(prev: dict | None, curr: dict) -> list[dict]:
    global _pending_direct_rerolls

    if not prev:
        return []

    events = []
    prev_run = prev.get("run", {})
    curr_run = curr.get("run", {})
    prev_state = prev.get("state", {})
    curr_state = curr.get("state", {})
    prev_player = prev.get("player", {})
    curr_player = curr.get("player", {})
    prev_state_name = prev_state.get("state")
    curr_state_name = curr_state.get("state")
    gold_delta = _numeric_delta(prev_player.get("Gold"), curr_player.get("Gold"))

    if prev_state_name and curr_state_name and prev_state_name != curr_state_name:
        events.append(
            _make_action_event(
                curr,
                "state_change",
                from_state=prev_state_name,
                to_state=curr_state_name,
            )
        )

    prev_selection = list(prev_state.get("selection_set") or [])
    curr_selection = list(curr_state.get("selection_set") or [])
    prev_selection = _normalized_selection(prev_selection)
    curr_selection = _normalized_selection(curr_selection)
    if prev_selection != curr_selection:
        events.append(
            _make_action_event(
                curr,
                "selection_change",
                previous=prev_selection,
                current=curr_selection,
                added=[value for value in curr_selection if value not in prev_selection],
                removed=[value for value in prev_selection if value not in curr_selection],
            )
        )

    prev_rerolls = prev_state.get("rerolls_remaining")
    curr_rerolls = curr_state.get("rerolls_remaining")
    if (
        isinstance(prev_rerolls, int)
        and isinstance(curr_rerolls, int)
        and curr_rerolls < prev_rerolls
    ):
        if _pending_direct_rerolls > 0:
            _pending_direct_rerolls -= 1
        else:
            events.append(
                _make_action_event(
                    curr,
                    "reroll",
                    previous_remaining=prev_rerolls,
                    current_remaining=curr_rerolls,
                    gold_delta=gold_delta,
                    previous_selection=prev_selection,
                    current_selection=curr_selection,
                )
            )

    prev_cards = _card_map(prev)
    curr_cards = _card_map(curr)
    all_instance_ids = sorted(set(prev_cards) | set(curr_cards))

    for instance_id in all_instance_ids:
        old = prev_cards.get(instance_id)
        new = curr_cards.get(instance_id)
        if old and new:
            old_category = old.get("category")
            new_category = new.get("category")
            old_socket = old.get("socket")
            new_socket = new.get("socket")

            if old_category == new_category and old_socket == new_socket:
                continue

            if old_category == "offered" and new_category in {"player_board", "player_stash"}:
                events.append(
                    _make_action_event(
                        curr,
                        "buy",
                        instance_id=instance_id,
                        template_id=new.get("template_id") or old.get("template_id"),
                        to_category=new_category,
                        to_socket=new_socket,
                        gold_delta=gold_delta,
                    )
                )
                continue

            if old_category == "offered" and new_category == "player_skills":
                events.append(
                    _make_action_event(
                        curr,
                        "skill_select",
                        instance_id=instance_id,
                        template_id=new.get("template_id") or old.get("template_id"),
                        gold_delta=gold_delta,
                    )
                )
                continue

            if old_category == "offered" and new_category == "opponent_board":
                events.append(
                    _make_action_event(
                        curr,
                        "event_choice",
                        instance_id=instance_id,
                        template_id=new.get("template_id") or old.get("template_id"),
                        to_socket=new_socket,
                    )
                )
                continue

            if old_category and new_category and old_category.startswith("player_") and new_category.startswith("player_"):
                events.append(
                    _make_action_event(
                        curr,
                        "move",
                        instance_id=instance_id,
                        template_id=new.get("template_id") or old.get("template_id"),
                        from_category=old_category,
                        to_category=new_category,
                        from_socket=old_socket,
                        to_socket=new_socket,
                    )
                )
                continue

        if old and not new and old.get("category", "").startswith("player_"):
            if (
                (_is_action_state(prev_state_name) or _is_action_state(curr_state_name))
                and isinstance(gold_delta, (int, float))
                and gold_delta > 0
            ):
                events.append(
                    _make_action_event(
                        curr,
                        "sell",
                        instance_id=instance_id,
                        template_id=old.get("template_id"),
                        from_category=old.get("category"),
                        from_socket=old.get("socket"),
                        gold_delta=gold_delta,
                    )
                )

    # Collapse duplicate sell events that can happen when an entire zone refreshes.
    deduped = []
    seen = set()
    for event in events:
        details = event.get("details", {})
        key = (
            event.get("event_type"),
            details.get("instance_id"),
            details.get("from_category"),
            details.get("to_category"),
            details.get("from_socket"),
            details.get("to_socket"),
            event.get("message_id"),
            event.get("snapshot_id"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(event)
    return deduped


def _format_action_event(event: dict) -> str:
    details = event.get("details", {})
    event_type = event.get("event_type")
    instance_id = details.get("instance_id")
    template_id = details.get("template_id")

    if event_type == "buy":
        target_label = (
            f"{details.get('to_category')}:{details.get('to_socket')}"
            if details.get("to_category") is not None
            else f"targets={details.get('target_sockets')}"
        )
        return (
            f"[Action #{event['event_seq']}] BUY  {instance_id}  "
            f"template={template_id}  -> {target_label}  "
            f"gold_delta={details.get('gold_delta')}"
        )
    if event_type == "sell":
        source_label = (
            f"{details.get('from_category')}:{details.get('from_socket')}"
            if details.get("from_category") is not None
            else f"targets={details.get('target_sockets')}"
        )
        return (
            f"[Action #{event['event_seq']}] SELL {instance_id}  "
            f"template={template_id}  from {source_label}  "
            f"gold_delta={details.get('gold_delta')}"
        )
    if event_type == "move":
        if details.get("from_category") is not None or details.get("to_category") is not None:
            move_label = (
                f"{details.get('from_category')}:{details.get('from_socket')} -> "
                f"{details.get('to_category')}:{details.get('to_socket')}"
            )
        else:
            move_label = f"targets={details.get('target_sockets')}"
        return (
            f"[Action #{event['event_seq']}] MOVE {instance_id}  "
            f"{move_label}"
        )
    if event_type == "skill_select":
        return (
            f"[Action #{event['event_seq']}] SKILL {instance_id}  "
            f"template={template_id}  targets={details.get('target_sockets')}"
        )
    if event_type == "event_choice":
        return (
            f"[Action #{event['event_seq']}] CHOOSE {instance_id}  "
            f"template={template_id}  -> {details.get('to_socket') or details.get('target_sockets')}"
        )
    if event_type == "reroll":
        return (
            f"[Action #{event['event_seq']}] REROLL  "
            f"{details.get('previous_remaining')} -> {details.get('current_remaining')} left  "
            f"gold_delta={details.get('gold_delta')}"
        )
    if event_type == "selection_change":
        return (
            f"[Action #{event['event_seq']}] SELECTION  "
            f"{details.get('previous')} -> {details.get('current')}"
        )
    if event_type == "state_change":
        return (
            f"[Action #{event['event_seq']}] STATE  "
            f"{details.get('from_state')} -> {details.get('to_state')}"
        )
    return f"[Action #{event['event_seq']}] {event_type.upper()} {json.dumps(details, default=str)}"


def _print_action_events(events: list[dict]):
    for event in events:
        print(_format_action_event(event))


def _context_snapshot_for_event() -> dict:
    return _last_action_snapshot or _last_merged_snapshot or {}


def _context_card_lookup(ctx: dict, instance_id: str | None) -> dict | None:
    if not instance_id:
        return None
    return _card_map(ctx or {}).get(instance_id)


# Re-enabled from W/L-only scope-down (F9): build enriched command event from raw JS event
def _build_direct_command_event(raw_event: dict) -> dict:
    global _pending_direct_rerolls

    ctx = _context_snapshot_for_event()
    existing = _context_card_lookup(ctx, raw_event.get("instance_id"))
    details = {
        "instance_id": raw_event.get("instance_id"),
        "command_class": raw_event.get("command_class"),
        "target_sockets": list(raw_event.get("target_sockets") or []),
        "section": raw_event.get("section"),
        "hook_source": raw_event.get("hook_source"),
    }
    if existing:
        details["template_id"] = existing.get("template_id")
        details["from_category"] = existing.get("category")
        details["from_socket"] = existing.get("socket")
    if details["target_sockets"]:
        details["to_socket"] = details["target_sockets"][0]
    event_type = raw_event.get("event_type") or "command"
    section = raw_event.get("section")
    if event_type in {"buy", "move"}:
        if section == 1:
            details["to_category"] = "player_stash"
        elif section == 0:
            details["to_category"] = "player_board"
        elif details.get("to_socket") is not None:
            details["to_category"] = "player_board"
    elif event_type == "skill_select":
        details["to_category"] = "player_skills"

    event = _make_action_event(
        ctx,
        event_type,
        **details,
    )
    event["captured_at"] = raw_event.get("timestamp", event.get("captured_at"))
    event["command_id"] = raw_event.get("command_id")
    if event["event_type"] == "reroll":
        _pending_direct_rerolls += 1
    return event


def on_message(message, data):
    """Handle messages from the Frida Mono agent.

    IMPORTANT: This callback runs on Frida's message-pump thread.  Blocking
    here back-pressures the agent's send() calls, which in turn stalls the
    game thread inside the hooked method.  Keep this as thin as possible â€”
    heavy work (snapshot processing, action inference, DB/file I/O) is
    dispatched to _db_queue for the background worker.
    """
    if message["type"] == "send":
        payload = message["payload"]
        msg_type = payload.get("type", "")

        if msg_type == "info":
            msg = payload["msg"]
            if _should_print_info(msg):
                print(f"[Mono] {msg}")
        elif msg_type == "error":
            print(f"[Mono] ERROR: {payload['msg']}")
        elif msg_type == "debug":
            msg = payload["msg"]
            if _should_print_debug(msg):
                print(f"[Mono] DEBUG: {msg}")
        elif msg_type == "ready":
            print(f"[Mono] {payload['msg']}")
        elif msg_type == "probe":
            handle_probe(payload)
        elif msg_type == "capture_call":
            handle_capture_call(payload)
        elif msg_type == "perf":
            _log_hook_perf(payload)
        elif msg_type == "batch":
            # Batched items from a single hook invocation â€” dispatch each.
            for item in payload.get("items", []):
                _dispatch_item(item)
        elif msg_type == "command_event":
            _dispatch_item(payload)
        elif msg_type == "game_state":
            _dispatch_item(payload)
        elif msg_type == "deferred_cards":
            # F1: deferred card data arrives after the snapshot â€” merge on background thread
            if _db_queue is not None:
                _db_queue.put(("deferred_cards", payload))
            else:
                handle_deferred_cards(payload)
        elif msg_type == "deferred_template_events":
            if _db_queue is not None:
                _db_queue.put(("deferred_template_events", payload))
            else:
                handle_deferred_template_events(payload)
        elif msg_type == "deferred_player_attrs":
            if _db_queue is not None:
                _db_queue.put(("deferred_player_attrs", payload))
            else:
                handle_deferred_player_attrs(payload)

    elif message["type"] == "error":
        print(f"[Mono] Script error: {message.get('description', message)}")


def _dispatch_item(item):
    """Route a single agent message to the background worker queue.

    For game_state and command_event, we push onto the queue so processing
    happens off the Frida message-pump thread.  Lightweight message types
    (probe, capture_call, info, etc.) are still handled inline.
    """
    msg_type = item.get("type", "")
    data = item.get("data", {}) or {}
    if msg_type == "game_state":
        if _db_queue is not None:
            _db_queue.put(("process_snapshot", data))
        else:
            # Fallback: process inline if queue not started (no --log/--db)
            handle_game_state(data)
    elif msg_type == "command_event":
        if _db_queue is not None:
            _db_queue.put(("process_command", data))
        else:
            handle_command_event(data)


def handle_probe(payload):
    """Track which GameStateHandler methods fire during gameplay."""
    method = payload.get("method", "?")
    _probe_hits[method] = _probe_hits.get(method, 0) + 1
    count = _probe_hits[method]
    if count <= 3:
        print(f"[Probe] GameStateHandler.{method}() fired (#{count})")
    elif count == 4:
        print(f"[Probe] GameStateHandler.{method}() fired (suppressing further...)")


def handle_capture_call(payload):
    """Track how often the hooked capture method fires and whether extraction succeeded."""
    method = payload.get("method", "?")
    count = int(payload.get("count", 0) or 0)
    status = payload.get("status", "?")
    _capture_calls[method] = count
    if _VERBOSE_HOOKS:
        print(f"[Capture] {method} call #{count} -> {status}")


def handle_command_event(raw_event):
    event = _build_direct_command_event(raw_event)
    _print_action_events([event])
    if (_do_log or _do_db):
        persist_action_event(event)


def handle_deferred_cards(payload):
    """F1: Merge deferred card data (from setImmediate) into the matching snapshot.

    The JS agent defers heavy card collection decoding off the game thread via
    setImmediate. When the decoded cards arrive here, store them in
    _deferred_cards_by_snapshot_id so that _merge_partial_snapshot (or
    handle_game_state) can pick them up. We also try to merge into
    _last_merged_snapshot if the IDs match.
    """
    global _deferred_cards_by_snapshot_id, _last_merged_snapshot
    snapshot_id = payload.get("snapshot_id")
    cards = payload.get("cards") or {}
    if not snapshot_id or not cards:
        return
    _prune_disabled_snapshot_cards(cards)
    # Store for future merges
    _deferred_cards_by_snapshot_id[snapshot_id] = cards
    # Cap size to avoid unbounded growth (keep last 32 snapshots)
    if len(_deferred_cards_by_snapshot_id) > 32:
        oldest = min(_deferred_cards_by_snapshot_id.keys())
        del _deferred_cards_by_snapshot_id[oldest]
    # If the matching snapshot is already in _last_merged_snapshot, merge now
    if _last_merged_snapshot and _last_merged_snapshot.get("id") == snapshot_id:
        for key in _CARD_LIST_KEYS:
            if cards.get(key):
                _last_merged_snapshot[key] = [dict(c) for c in cards[key]]
        _apply_event_template_recovery(_last_merged_snapshot)
        # Persist updated snapshot with card data
        if _do_log or _do_db:
            persist_snapshot(_last_merged_snapshot)
        if _VERBOSE_HOOKS:
            total = sum(len(cards.get(k, [])) for k in _CARD_LIST_KEYS)
            print(f"[Mono] Deferred cards merged into snapshot #{snapshot_id}: {total} cards")


def handle_deferred_template_events(payload):
    """Merge deferred GameSim template events into the matching snapshot."""
    global _deferred_template_events_by_snapshot_id, _last_merged_snapshot
    snapshot_id = payload.get("snapshot_id")
    template_events = payload.get("card_template_events") or []
    if not snapshot_id or not template_events:
        return

    _deferred_template_events_by_snapshot_id[snapshot_id] = list(template_events)
    if len(_deferred_template_events_by_snapshot_id) > 64:
        oldest = min(_deferred_template_events_by_snapshot_id.keys())
        del _deferred_template_events_by_snapshot_id[oldest]

    if _last_merged_snapshot and _last_merged_snapshot.get("id") == snapshot_id:
        _last_merged_snapshot["card_template_events"] = list(template_events)
        _apply_event_template_recovery(_last_merged_snapshot)
        if _do_log or _do_db:
            persist_snapshot(_last_merged_snapshot)
        if _VERBOSE_HOOKS:
            print(
                f"[Mono] Deferred template events merged into snapshot #{snapshot_id}: "
                f"{len(template_events)} events"
            )


def handle_deferred_player_attrs(payload):
    """Merge deferred Player.Attributes (from setImmediate) into the matching snapshot.

    Mirrors handle_deferred_cards: store by snapshot_id so _merge_partial_snapshot
    can pick it up if it arrives before the snapshot, and also merge in-place
    into _last_merged_snapshot + re-persist on late arrival.
    """
    global _deferred_attrs_by_snapshot_id, _last_merged_snapshot
    global _deferred_attrs_late_arrival_count, _deferred_attrs_dropped_count
    snapshot_id = payload.get("snapshot_id")
    attrs = payload.get("attrs") or {}
    if not snapshot_id or not attrs:
        _deferred_attrs_dropped_count += 1
        _maybe_log_deferred_attrs_stats()
        return

    _deferred_attrs_by_snapshot_id[snapshot_id] = attrs
    if len(_deferred_attrs_by_snapshot_id) > 128:
        oldest = min(_deferred_attrs_by_snapshot_id.keys())
        del _deferred_attrs_by_snapshot_id[oldest]
        _deferred_attrs_dropped_count += 1

    if _last_merged_snapshot and _last_merged_snapshot.get("id") == snapshot_id:
        player = _last_merged_snapshot.setdefault("player", {})
        for k, v in attrs.items():
            player[k] = v
        _deferred_attrs_late_arrival_count += 1
        if _do_log or _do_db:
            persist_snapshot(_last_merged_snapshot)
        if _VERBOSE_HOOKS:
            print(
                f"[Mono] Deferred player attrs merged (late) into snapshot #{snapshot_id}: "
                f"{len(attrs)} attrs"
            )
    _maybe_log_deferred_attrs_stats()


def _maybe_log_deferred_attrs_stats():
    """Emit a periodic summary so broken deferred-attrs flow is visible in logs.

    Signals to watch:
      - pickup=0 and late=0 → feature not wiring up (messages arriving but no merges).
      - dropped growing fast → attrs piling up without matching snapshots.
      - pending dict size near cap → eviction is stealing attrs before snapshots arrive.
    """
    global _deferred_attrs_last_stat_log_ms
    now_ms = _perf_now_ms()
    if now_ms - _deferred_attrs_last_stat_log_ms < _DEFERRED_ATTRS_STAT_INTERVAL_MS:
        return
    _deferred_attrs_last_stat_log_ms = now_ms
    pending = len(_deferred_attrs_by_snapshot_id)
    total_applied = _deferred_attrs_pickup_count + _deferred_attrs_late_arrival_count
    if total_applied == 0 and _deferred_attrs_dropped_count == 0 and pending == 0:
        return
    print(
        f"[Mono] deferred_player_attrs stats: "
        f"pickup={_deferred_attrs_pickup_count} "
        f"late_arrival={_deferred_attrs_late_arrival_count} "
        f"dropped={_deferred_attrs_dropped_count} "
        f"pending={pending}"
    )


def _snapshot_dedupe_key(gs):
    """Return a stable dedupe key for a game-state message."""
    message_id = gs.get("message_id")
    if message_id:
        return f"msg:{message_id}"

    canonical = {
        "run": gs.get("run", {}),
        "state": gs.get("state", {}),
        "player": gs.get("player", {}),
        "offered": gs.get("offered", []),
        "player_board": gs.get("player_board", []),
        "player_stash": gs.get("player_stash", []),
        "player_skills": gs.get("player_skills", []),
        "opponent_board": gs.get("opponent_board", []),
    }
    digest = hashlib.sha1(
        json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    return f"sha1:{digest}"


def _snapshot_db_queue_key(gs: dict) -> str | None:
    """Return a stable key so repeated DB writes for one snapshot can coalesce."""
    if not isinstance(gs, dict):
        return None
    snap_id = gs.get("id")
    if snap_id is not None:
        return f"id:{snap_id}"
    message_id = gs.get("message_id")
    if message_id:
        return f"msg:{message_id}"
    return _snapshot_dedupe_key(gs)


def _merge_partial_snapshot(gs):
    """Overlay dynamic partial updates onto the most recent captured state.

    SCOPED DOWN: W/L-only mode. The run/state/player merging logic is preserved
    (needed for W/L correlation). The card-reconciliation sections below are
    no-ops because cards are always empty lists, but kept for easy re-enable.
    """
    global _last_merged_snapshot, _deferred_attrs_pickup_count

    merged = {
        **gs,
        "run": dict(gs.get("run", {})),
        "state": dict(gs.get("state", {})),
        "player": dict(gs.get("player", {})),
    }
    for key in _CARD_LIST_KEYS:
        merged[key] = [dict(card) for card in gs.get(key, [])]

    if "dynamic-data" in str(merged.get("hook_source", "")) and _last_merged_snapshot:
        prev = _last_merged_snapshot
        merged["run"] = {**prev.get("run", {}), **merged.get("run", {})}
        merged["state"] = {**prev.get("state", {}), **merged.get("state", {})}
        merged["player"] = {**prev.get("player", {}), **merged.get("player", {})}
        prev_hp = prev.get("player", {}).get("Health")
        prev_hp_max = prev.get("player", {}).get("HealthMax")
        curr_hp = gs.get("player", {}).get("Health")
        curr_hp_max = gs.get("player", {}).get("HealthMax")

        # Some dynamic GameSim snapshots regress HP to the hero's baseline
        # 300/300 even after health has scaled up for the run. Preserve the
        # richer prior values instead of letting the baseline overwrite them.
        if (
            prev_hp_max not in (None, 0)
            and curr_hp == 300
            and curr_hp_max == 300
            and isinstance(prev_hp_max, (int, float))
            and prev_hp_max > 300
        ):
            merged["player"]["Health"] = prev_hp
            merged["player"]["HealthMax"] = prev_hp_max
        for key in _PERSISTENT_CARD_KEYS:
            if not merged.get(key) and prev.get(key):
                merged[key] = [dict(card) for card in prev.get(key, [])]
        if not any(merged.get(key) for key in _CARD_LIST_KEYS):
            for key in _CARD_LIST_KEYS:
                merged[key] = [dict(card) for card in prev.get(key, [])]

    # Dynamic card deltas often mention only the category a card moved into.
    # If the same instance appears in the current delta under one category,
    # evict any stale copies from all other categories carried forward from
    # previous snapshots before we persist or infer actions.
    current_card_by_instance = {}
    current_category_by_instance = {}
    for key in _CARD_LIST_KEYS:
        for card in gs.get(key, []) or []:
            instance_id = (card or {}).get("instance_id")
            if instance_id:
                current_card_by_instance[instance_id] = dict(card)
                current_category_by_instance[instance_id] = key

    if current_category_by_instance:
        for key in _CARD_LIST_KEYS:
            reconciled = []
            for card in merged.get(key, []) or []:
                instance_id = (card or {}).get("instance_id")
                if not instance_id:
                    reconciled.append(card)
                    continue
                winning_category = current_category_by_instance.get(instance_id)
                if winning_category and winning_category != key:
                    winning_card = current_card_by_instance.get(instance_id, {})
                    if (
                        winning_category == "offered"
                        and key in _PERSISTENT_CARD_KEYS
                        and not winning_card.get("owner")
                        and winning_card.get("section") in (None, "", "None")
                        and winning_card.get("socket") in (None, "", "None")
                    ):
                        reconciled.append(card)
                        continue
                    continue
                reconciled.append(card)
            merged[key] = reconciled

    # Even outside the immediate delta, player/opponent ownership should win
    # over "offered" when the same instance leaks into multiple categories.
    owner_category_by_instance = {}
    for key in ("player_board", "player_stash", "player_skills", "opponent_board"):
        for card in merged.get(key, []) or []:
            instance_id = (card or {}).get("instance_id")
            if instance_id:
                owner_category_by_instance[instance_id] = key

    if owner_category_by_instance:
        merged["offered"] = [
            card
            for card in (merged.get("offered", []) or [])
            if (card or {}).get("instance_id") not in owner_category_by_instance
        ]

    # F1: pull in any deferred card data that arrived before this snapshot processed
    snap_id = gs.get("id")
    if snap_id and snap_id in _deferred_cards_by_snapshot_id:
        deferred = _deferred_cards_by_snapshot_id.pop(snap_id)
        for key in _CARD_LIST_KEYS:
            if deferred.get(key):
                merged[key] = [dict(c) for c in deferred[key]]
    if snap_id and snap_id in _deferred_template_events_by_snapshot_id:
        merged["card_template_events"] = list(
            _deferred_template_events_by_snapshot_id.pop(snap_id)
        )
    # Pull in any deferred player attrs that arrived before this snapshot processed.
    # Common case: attrs arrive AFTER the snapshot (handled by handle_deferred_player_attrs);
    # this branch handles the race where setImmediate fires before the worker dequeues.
    if snap_id and snap_id in _deferred_attrs_by_snapshot_id:
        deferred_attrs = _deferred_attrs_by_snapshot_id.pop(snap_id)
        player_dict = merged.setdefault("player", {})
        for k, v in deferred_attrs.items():
            player_dict[k] = v
        _deferred_attrs_pickup_count += 1

    _prune_disabled_snapshot_cards(merged)
    _apply_event_template_recovery(merged)

    _last_merged_snapshot = {
        **merged,
        "run": dict(merged.get("run", {})),
        "state": dict(merged.get("state", {})),
        "player": dict(merged.get("player", {})),
    }
    for key in _CARD_LIST_KEYS:
        _last_merged_snapshot[key] = [dict(card) for card in merged.get(key, [])]
    return merged


def handle_game_state(gs):
    """Process a captured game state snapshot.

    SCOPED DOWN: W/L-only mode. Tracks victories, defeats, prestige, hero, day.
    Card enumeration, action event inference, selection set, and detailed board
    rendering are all commented out. Re-enable by restoring the commented blocks.
    """
    global _snapshot_count, _duplicate_snapshot_count, _last_action_snapshot
    gs = _merge_partial_snapshot(gs)
    _prune_disabled_snapshot_cards(gs)
    # QW5: normalize numeric timestamp (Date.now() ms) to ISO string
    _ts = gs.get("timestamp")
    if isinstance(_ts, (int, float)):
        gs["timestamp"] = datetime.datetime.fromtimestamp(
            _ts / 1000.0, tz=datetime.timezone.utc
        ).isoformat()
    dedupe_key = _snapshot_dedupe_key(gs)
    if dedupe_key in _seen_snapshot_keys:
        _duplicate_snapshot_count += 1
        if _VERBOSE_HOOKS:
            print(
                f"[Mono] Duplicate snapshot skipped "
                f"({gs.get('hook', '?')}, {gs.get('message_id') or dedupe_key})"
            )
        return

    _seen_snapshot_keys.add(dedupe_key)
    _snapshot_count += 1

    # Re-enabled from W/L-only scope-down (F2): action event inference
    action_events = _infer_action_events(_last_action_snapshot, gs)
    _last_action_snapshot = _clone_snapshot_for_actions(gs)
    if action_events:
        gs["action_events"] = action_events

    if not _should_render_snapshot(gs):
        if _do_log or _do_db:
            persist_snapshot(gs)
        return

    snap_id = gs.get("id", _snapshot_count)
    run = gs.get("run", {})
    state = gs.get("state", {})
    player = gs.get("player", {})

    state_name = state.get("state", "?")
    hero = player.get("hero", "?")
    day = run.get("day", "?")
    hour = run.get("hour", "?")
    message_id = gs.get("message_id")
    gold = player.get("Gold", "?")
    hp = player.get("Health", "?")
    hp_max = player.get("HealthMax", "?")
    prestige = player.get("Prestige", "?")
    wins = run.get("victories", 0)
    losses = run.get("defeats", 0)

    render_sig = _render_signature(gs)
    if render_sig in _rendered_snapshot_keys:
        if _do_log or _do_db:
            persist_snapshot(gs)
        return
    _rendered_snapshot_keys.add(render_sig)

    global _last_snapshot_print_ms, _snapshot_prints_suppressed

    # Rate-limit verbose prints so bursty GameSim deltas don't flood the
    # console. The compact (default) form emits a single line per snapshot
    # with the same essential info; only the legacy multi-line block is
    # throttled.
    now_ms = time.time() * 1000.0
    if _COMPACT_SNAPSHOTS:
        msg_tag = f" msg={message_id}" if message_id else ""
        print(
            f"[Mono] [#{snap_id}]{msg_tag} {state_name} | {hero}"
            f" Day {day} Hour {hour} | Gold: {gold} HP: {hp}/{hp_max}"
            f" Prestige: {prestige} PvP: {wins}W/{losses}L"
        )
    else:
        should_emit = (
            _SNAPSHOT_PRINT_MIN_INTERVAL_MS <= 0
            or (now_ms - _last_snapshot_print_ms) >= _SNAPSHOT_PRINT_MIN_INTERVAL_MS
            or state_name in _INTERESTING_RENDER_STATES
        )
        if should_emit:
            lines = [f"\n{'=' * 60}"]
            header = f"  [#{snap_id}"
            if message_id:
                header += f" | msg={message_id}"
            header += f"] {state_name}  |  {hero}  Day {day} Hour {hour}"
            lines.append(header)
            lines.append(
                f"  Gold: {gold}  HP: {hp}/{hp_max}  Prestige: {prestige}"
                f"  PvP: {wins}W/{losses}L"
            )
            if _snapshot_prints_suppressed > 0:
                lines.append(
                    f"  (+{_snapshot_prints_suppressed} suppressed snapshots"
                    f" since last verbose block)"
                )
                _snapshot_prints_suppressed = 0
            lines.append(f"{'=' * 60}\n")
            print("\n".join(lines))
            _last_snapshot_print_ms = now_ms
        else:
            _snapshot_prints_suppressed += 1

    # Persist directly â€” we're already on the background worker thread.
    if _do_log or _do_db:
        persist_snapshot(gs)


def start_db_writer():
    """Initialize API tables once and start a background worker thread.

    The worker handles both persistence AND snapshot/command processing so
    that on_message can return immediately and unblock Frida's message pump.
    The queue is always created (even without --log/--db) so that processing
    can be offloaded regardless.
    """
    global _db_queue, _db_thread, _api_log_module, _do_db

    if _do_db:
        try:
            import api_log
            api_log.init_api_tables()
            api_log.init_api_tables = lambda: None
            _api_log_module = api_log
        except ImportError:
            print("[Mono] WARNING: api_log.py not found - skipping DB write")
            _do_db = False
        except Exception as e:
            print(f"[Mono] WARNING: DB init failed - skipping DB write ({e})")
            _do_db = False

    _db_queue = queue.Queue()

    def _describe_payload(kind, payload):
        if not isinstance(payload, dict):
            return f"type={type(payload).__name__}"

        parts = []
        if payload.get("id") is not None:
            parts.append(f"id={payload.get('id')}")
        if payload.get("snapshot_id") is not None:
            parts.append(f"snapshot_id={payload.get('snapshot_id')}")
        if payload.get("message_id") is not None:
            parts.append(f"message_id={payload.get('message_id')}")
        if payload.get("event_seq") is not None:
            parts.append(f"event_seq={payload.get('event_seq')}")
        if payload.get("event_type"):
            parts.append(f"event_type={payload.get('event_type')}")

        state = payload.get("state")
        if isinstance(state, dict) and state.get("state"):
            parts.append(f"state={state.get('state')}")
        run = payload.get("run")
        if isinstance(run, dict):
            if run.get("day") is not None:
                parts.append(f"day={run.get('day')}")
            if run.get("hour") is not None:
                parts.append(f"hour={run.get('hour')}")

        details = payload.get("details")
        if isinstance(details, dict):
            interesting = {}
            for key in ("decision_id", "offered", "rejected", "inferred_purchase", "rerolls"):
                if key in details:
                    interesting[key] = details[key]
            if interesting:
                parts.append(f"details={interesting}")

        return " ".join(parts) if parts else "dict"

    def _worker():
        while True:
            item = _db_queue.get()
            kind = "snapshot"
            payload = item
            try:
                if item is None:
                    return
                if isinstance(item, tuple):
                    kind, payload = item
                else:
                    kind, payload = "snapshot", item
                if kind == "process_snapshot":
                    # Full processing: merge, dedup, infer actions, render, persist
                    handle_game_state(payload)
                elif kind == "process_command":
                    # Full processing: build event, render, persist
                    handle_command_event(payload)
                elif kind == "deferred_cards":
                    # F1: merge deferred card data into matching snapshot
                    handle_deferred_cards(payload)
                elif kind == "deferred_template_events":
                    handle_deferred_template_events(payload)
                elif kind == "deferred_player_attrs":
                    handle_deferred_player_attrs(payload)
                elif kind == "snapshot":
                    persist_snapshot(payload)
                elif kind == "snapshot_db":
                    actual_payload = payload
                    if isinstance(payload, dict) and payload.get("_snapshot_db_key"):
                        queue_key = payload.get("_snapshot_db_key")
                        actual_payload = _pending_snapshot_db_by_key.pop(queue_key, None)
                        _pending_snapshot_db_keys.discard(queue_key)
                    if actual_payload:
                        payload = actual_payload
                        _store_game_state_to_db_impl(actual_payload)
                elif kind == "action_event":
                    persist_action_event(payload)
            except Exception as e:
                queue_depth = _db_queue.qsize() if _db_queue is not None else 0
                print(
                    f"[Mono] Persist error: kind={kind} queue_depth={queue_depth} "
                    f"payload={_describe_payload(kind, payload)} err={e}"
                )
                if "locked" in str(e).lower():
                    print(
                        f"[Mono] Persist lock detail: kind={kind} queue_depth={queue_depth} "
                        f"payload={_describe_payload(kind, payload)}"
                    )
            finally:
                _db_queue.task_done()

    _db_thread = threading.Thread(target=_worker, name="mono-db-writer", daemon=True)
    _db_thread.start()


def stop_db_writer():
    """Flush and stop the background DB writer thread."""
    global _db_queue, _db_thread, _mono_db_conn

    if _db_queue is None:
        return

    _db_queue.put(None)
    _db_queue.join()
    if _db_thread is not None:
        _db_thread.join(timeout=2.0)
    _db_queue = None
    _db_thread = None

    if _mono_db_conn is not None:
        try:
            _mono_db_conn.commit()
            _mono_db_conn.close()
        except Exception:
            pass
        _mono_db_conn = None

    _pending_snapshot_db_by_key.clear()
    _pending_snapshot_db_keys.clear()


def persist_snapshot(gs):
    """Write snapshot artifacts on the background worker.

    SCOPED DOWN: W/L-only mode. Persists api_game_states (victories, defeats,
    prestige, hero, day, run_state). api_cards inserts and action event logging
    are disabled. Re-enable by restoring the commented blocks.
    """
    run = gs.get("run", {})
    state = gs.get("state", {})
    player = gs.get("player", {})
    # SCOPED OUT: offered/board counts and action_events not tracked in W/L-only mode
    # offered = gs.get("offered", [])
    # board = gs.get("player_board", [])
    # action_events = gs.get("action_events", [])

    if _do_log and _output_dir:
        snap_id = gs.get("id", 0)
        json_path = _output_dir / f"state_{snap_id:03d}.json"
        json_path.write_text(json.dumps(gs, indent=2, default=str))

        if _log_file:
            entry = {
                "ts": gs.get("timestamp", datetime.datetime.now().isoformat()),
                "id": snap_id,
                "message_id": gs.get("message_id"),
                "state": state.get("state"),
                "hero": player.get("hero"),
                "day": run.get("day"),
                "victories": run.get("victories"),
                "defeats": run.get("defeats"),
                "gold": player.get("Gold"),
                "hp": player.get("Health"),
                "prestige": player.get("Prestige"),
            }
            _log_file.write(json.dumps(entry) + "\n")
            # SCOPED OUT: action event logging disabled for W/L-only mode
            # for event in action_events: ...
            _log_file.flush()

    if _do_db:
        store_game_state_to_db(gs)


def persist_action_event(event):
    if _do_log and _log_file:
        _log_file.write(
            json.dumps(
                {
                    "ts": event.get("captured_at"),
                    "type": "action",
                    "event_seq": event.get("event_seq"),
                    "snapshot_id": event.get("snapshot_id"),
                    "message_id": event.get("message_id"),
                    "event_type": event.get("event_type"),
                    "details": event.get("details", {}),
                },
                default=str,
            )
            + "\n"
        )
        _log_file.flush()
    if _do_db:
        store_action_event_to_db(event)


def store_game_state_to_db(gs):
    """Enqueue snapshot persistence work for the mono DB writer thread."""
    global _coalesced_snapshot_db_updates
    if _db_queue is None:
        _store_game_state_to_db_impl(gs)
        return
    queue_key = _snapshot_db_queue_key(gs)
    if not queue_key:
        _db_queue.put(("snapshot_db", gs))
        return

    _pending_snapshot_db_by_key[queue_key] = gs
    if queue_key in _pending_snapshot_db_keys:
        _coalesced_snapshot_db_updates += 1
        return

    _pending_snapshot_db_keys.add(queue_key)
    _db_queue.put((
        "snapshot_db",
        {
            "_snapshot_db_key": queue_key,
            "id": gs.get("id"),
            "message_id": gs.get("message_id"),
            "state": gs.get("state"),
        },
    ))


def _is_suspicious_template_id(template_id: str) -> bool:
    if not template_id:
        return False
    template_id = str(template_id).lower()
    if template_id == "00000000-0000-0000-0000-000000000000":
        return True
    return template_id.endswith("-0000-0000-0000-000000000000") or template_id.endswith("-0000-0000-000000000000")


def _update_event_template_cache(gs: dict) -> dict[str, str]:
    """Capture authoritative instance->template pairs from GameSim events."""
    event_map: dict[str, str] = {}
    for event in gs.get("card_template_events") or []:
        if not isinstance(event, dict):
            continue
        instance_id = event.get("instance_id")
        template_id = event.get("template_id")
        if not instance_id or not template_id:
            continue
        if _is_suspicious_template_id(template_id):
            continue
        event_map[instance_id] = template_id
        _event_template_ids_by_instance.pop(instance_id, None)
        _event_template_ids_by_instance[instance_id] = template_id

    while len(_event_template_ids_by_instance) > 4096:
        oldest = next(iter(_event_template_ids_by_instance))
        _event_template_ids_by_instance.pop(oldest, None)

    return event_map


def _apply_event_template_recovery(gs: dict) -> None:
    """Repair suspicious card template IDs using recent GameSim spawn/deal events."""
    event_map = _update_event_template_cache(gs)
    if not _event_template_ids_by_instance and not event_map:
        return

    template_lookup = dict(_event_template_ids_by_instance)
    if event_map:
        template_lookup.update(event_map)

    recovered = []
    for category in _CARD_LIST_KEYS:
        for card in gs.get(category, []) or []:
            if not isinstance(card, dict):
                continue
            instance_id = card.get("instance_id")
            if not instance_id:
                continue
            recovered_template = template_lookup.get(instance_id)
            if not recovered_template:
                continue
            current_template = card.get("template_id")
            if current_template == recovered_template:
                continue
            if current_template and not _is_suspicious_template_id(current_template):
                continue
            card["template_id"] = recovered_template
            card["_template_recovered_from_event"] = "gamesim_event"
            recovered.append({
                "instance_id": instance_id,
                "category": category,
                "from": current_template or "<blank>",
                "to": recovered_template,
            })

    if recovered:
        print(
            f"[Mono] Recovered template ids from GameSim events "
            f"snapshot_id={gs.get('id')} message_id={gs.get('message_id')} "
            f"count={len(recovered)} sample={json.dumps(recovered[:8], default=str)}"
        )


def _infer_synthetic_event_category(gs: dict, event: dict) -> str | None:
    """Infer an api_cards category for a template event when no card snapshot exists."""
    state_name = (gs.get("state") or {}).get("state")
    if state_name in {
        "Choice",
        "Loot",
        "LevelUp",
        "Pedestal",
        "Encounter",
        "EndRunVictory",
        "EndRunDefeat",
    }:
        return "offered"
    return None


def _build_synthetic_event_card_rows(gs_id: int, gs: dict, existing_rows: list[tuple]) -> list[tuple]:
    """Create fallback api_cards rows from GameSim template events.

    This covers runs where the dynamic Cards collection yields 0 decoded cards
    but GameSim events still provide authoritative instance/template pairs.
    """
    card_by_instance: dict[str, tuple] = {}
    for row in existing_rows:
        instance_id = row[1]
        template_id = row[2]
        if not instance_id:
            continue
        if instance_id not in card_by_instance:
            card_by_instance[instance_id] = row
            continue
        prev_template = card_by_instance[instance_id][2]
        if _is_suspicious_template_id(prev_template) and not _is_suspicious_template_id(template_id):
            card_by_instance[instance_id] = row

    synthetic_rows: list[tuple] = []
    synthetic_log: list[dict] = []
    for event in gs.get("card_template_events") or []:
        if not isinstance(event, dict):
            continue
        instance_id = event.get("instance_id")
        template_id = event.get("template_id")
        if not instance_id or not template_id or _is_suspicious_template_id(template_id):
            continue
        category = _infer_synthetic_event_category(gs, event)
        if not category:
            continue

        existing = card_by_instance.get(instance_id)
        if existing and existing[2] and not _is_suspicious_template_id(existing[2]):
            continue

        row = (
            gs_id,
            instance_id,
            template_id,
            event.get("card_type"),
            None,
            None,
            None,
            None,
            None,
            category,
        )
        card_by_instance[instance_id] = row
        synthetic_rows.append(row)
        synthetic_log.append(
            {
                "instance_id": instance_id,
                "template_id": template_id,
                "category": category,
                "event_type": event.get("event_type"),
            }
        )

    if synthetic_log:
        print(
            f"[Mono] Synthesized api_cards from GameSim events "
            f"snapshot_id={gs.get('id')} message_id={gs.get('message_id')} "
            f"state={(gs.get('state') or {}).get('state')} "
            f"count={len(synthetic_log)} sample={json.dumps(synthetic_log[:8], default=str)}"
        )

    return synthetic_rows


def _log_suspicious_snapshot_cards(gs):
    suspicious = []
    for category, cards in [
        ("offered", gs.get("offered", [])),
        ("player_board", gs.get("player_board", [])),
        ("player_stash", gs.get("player_stash", [])),
        ("player_skills", gs.get("player_skills", [])),
        ("opponent_board", gs.get("opponent_board", [])),
    ]:
        for card in cards or []:
            template_id = card.get("template_id")
            if not _is_suspicious_template_id(template_id):
                continue
            suspicious.append({
                "category": category,
                "instance_id": card.get("instance_id"),
                "template_id": template_id,
                "card_type": card.get("type"),
                "owner": card.get("owner"),
                "section": card.get("section"),
                "socket": card.get("socket"),
                "debug_source": card.get("_debug_source"),
                "probe": card.get("_debug_probe"),
            })
    if not suspicious:
        return
    run = gs.get("run", {})
    state = gs.get("state", {})
    print(
        f"[Mono] Suspicious template ids in snapshot "
        f"snapshot_id={gs.get('id')} message_id={gs.get('message_id')} "
        f"state={state.get('state')} day={run.get('day')} hour={run.get('hour')} "
        f"selection_set={state.get('selection_set')} count={len(suspicious)} "
        f"event_template_count={len(gs.get('card_template_events') or [])} "
        f"cards={json.dumps(suspicious[:8], default=str)}"
    )


def _store_game_state_to_db_impl(gs):
    """Write the captured game state to api_game_states / api_cards tables."""
    try:
        import api_log
        api_log.init_api_tables()
    except ImportError:
        print("[Mono] WARNING: api_log.py not found â€” skipping DB write")
        return

    from datetime import datetime, timezone
    import sqlite3

    attempts = 5
    for attempt in range(1, attempts + 1):
        conn = _get_mono_conn()
        now = datetime.now(timezone.utc).isoformat()

        run = gs.get("run", {})
        state = gs.get("state", {})
        player = gs.get("player", {})

        captured_at = gs.get("timestamp") or now
        _log_suspicious_snapshot_cards(gs)

        try:
            cur = conn.execute("""
                INSERT INTO api_game_states
                    (message_id, captured_at, run_state, hero, day, hour,
                     victories, defeats, gold, health, health_max, level,
                     data_version, offered_count, board_count, stash_count,
                     skills_count, opponent_count, selection_set,
                     reroll_cost, rerolls_remaining, full_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
            """, (
                None,
                captured_at,
                state.get("state"),
                player.get("hero"),
                run.get("day"),
                run.get("hour"),
                run.get("victories"),
                run.get("defeats"),
                player.get("Gold"),
                player.get("Health"),
                player.get("HealthMax"),
                player.get("Level"),
                run.get("data_version"),
                len(gs.get("offered", [])),
                len(gs.get("player_board", [])),
                len(gs.get("player_stash", [])),
                len(gs.get("player_skills", [])),
                len(gs.get("opponent_board", [])),
                json.dumps(state.get("selection_set")) if state.get("selection_set") else None,
                state.get("reroll_cost"),
                state.get("rerolls_remaining"),
                json.dumps(gs, default=str),
            ))
            gs_id = cur.fetchone()[0]

            # Re-enabled from W/L-only scope-down (F8): api_cards table inserts
            card_rows = []
            for category, cards in [
                ("offered", gs.get("offered", [])),
                ("player_board", gs.get("player_board", [])),
                ("player_stash", gs.get("player_stash", [])),
                ("player_skills", gs.get("player_skills", [])),
                ("opponent_board", gs.get("opponent_board", [])),
            ]:
                for c in (cards or []):
                    card_rows.append((gs_id, c.get("instance_id"), c.get("template_id"),
                                      c.get("type"), c.get("tier"), c.get("size"),
                                      c.get("owner"), c.get("section"), c.get("socket"), category))
            synthetic_rows = _build_synthetic_event_card_rows(gs_id, gs, card_rows)
            if synthetic_rows:
                card_rows.extend(synthetic_rows)
            if card_rows:
                conn.executemany("""
                    INSERT INTO api_cards (game_state_id, instance_id, template_id, card_type,
                                           tier, size, owner, section, socket, category)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, card_rows)

            conn.commit()
            return
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower() or attempt == attempts:
                raise
            try:
                conn.rollback()
            except Exception:
                pass
            backoff_s = 0.15 * attempt
            print(
                f"[Mono] Snapshot DB busy; retry {attempt}/{attempts - 1} "
                f"after {backoff_s:.2f}s for snapshot id={gs.get('id')} "
                f"message_id={gs.get('message_id')}"
            )
            time.sleep(backoff_s)


def store_action_event_to_db(event):
    # Disabled: action events are sourced from run_state (Pipeline A)
    return


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# PROCESS FINDING (reused from capture_frida.py)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def list_game_processes(process_name="TheBazaar.exe"):
    try:
        script = (
            f"$p = Get-CimInstance Win32_Process -Filter \\\"name='{process_name}'\\\" | "
            "Select-Object ProcessId, CreationDate, CommandLine; "
            "if ($p) { $p | ConvertTo-Json -Compress }"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=8,
        )
        stdout = result.stdout.strip()
        if stdout:
            data = json.loads(stdout)
            if isinstance(data, dict):
                data = [data]
            return [{"pid": int(item["ProcessId"]),
                     "created": item.get("CreationDate"),
                     "command_line": item.get("CommandLine") or ""}
                    for item in data]
    except Exception:
        pass
    return []


def find_game_pid(process_name="TheBazaar.exe"):
    processes = list_game_processes(process_name)
    if processes:
        processes.sort(key=lambda p: (p.get("created") or "", p["pid"]))
        return processes[-1]["pid"]

    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {process_name}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.strip().split("\n"):
            if process_name.replace(".exe", "") in line:
                parts = line.split(",")
                if len(parts) >= 2:
                    return int(parts[1].strip('"'))
    except FileNotFoundError:
        pass
    return None


def wait_for_game_pid(process_name="TheBazaar.exe", poll_seconds=1.0, settle_seconds=8.0):
    print(f"[Mono] Waiting for {process_name} to start...")
    first_seen_at = None
    chosen_pid = None
    last_status_at = 0.0

    while True:
        candidate_pid = find_game_pid(process_name)

        if candidate_pid is not None:
            now = time.time()
            if chosen_pid != candidate_pid:
                chosen_pid = candidate_pid
                first_seen_at = now
                print(f"[Mono] Detected {process_name} PID {candidate_pid}; waiting for startup settle...")
            elif first_seen_at is not None and now - first_seen_at >= settle_seconds:
                print(f"[Mono] Selected PID {chosen_pid} after startup settle.")
                return chosen_pid
        else:
            first_seen_at = None
            chosen_pid = None
            now = time.time()
            if now - last_status_at >= 5:
                print(f"[Mono] Still waiting for {process_name}...")
                last_status_at = now

        time.sleep(poll_seconds)


def main():
    global _output_dir, _do_log, _do_db, _log_file
    global _VERBOSE_DEBUG, _VERBOSE_HOOKS, _RENDER_ALL_SNAPSHOTS, _DETAILED_SNAPSHOTS, _FULL_DELTA_CARDS
    global _DELTA_PLAYER_ATTRS, _ACTION_EVENT_CARDS, _CAPTURE_OPPONENT_BOARD, _ENABLE_PROBES, _ENABLE_BROAD_HOOKS
    global _COMPACT_SNAPSHOTS, _SNAPSHOT_PRINT_MIN_INTERVAL_MS

    parser = argparse.ArgumentParser(
        description="Mono-hooking Frida capture for The Bazaar â€” "
                    "reads game state directly from managed C# objects"
    )
    parser.add_argument("--pid", type=int, default=None,
                        help="PID of TheBazaar.exe (auto-detected if not specified)")
    parser.add_argument("--log", action="store_true",
                        help="Save captured game states to disk")
    parser.add_argument("--db", action="store_true",
                        help="Write captured states to SQLite (api_game_states table)")
    parser.add_argument("--process", type=str, default="TheBazaar.exe",
                        help="Process name to attach to")
    parser.add_argument("--wait", action="store_true",
                        help="Wait for game to launch before attaching")
    parser.add_argument("--verbose-hooks", action="store_true",
                        help="Print per-hook capture calls and duplicate-skip messages")
    parser.add_argument("--verbose-debug", action="store_true",
                        help="Print selected debug messages from the Frida reader")
    parser.add_argument("--all-snapshots", action="store_true",
                        help="Print every captured snapshot instead of only choice-like states")
    parser.add_argument("--detailed-snapshots", action="store_true",
                        help="Print full offered/board/skill/opponent template details for rendered snapshots")
    parser.add_argument("--full-delta-cards", action="store_true",
                        help="Fully decode card collections on every GameSim delta (slower, more complete)")
    parser.add_argument("--delta-player-attrs", action="store_true",
                        help="Decode dynamic player attributes on every GameSim delta (slower, richer Gold/HP)")
    parser.add_argument("--action-delta-cards", action="store_true",
                        help="Also decode action-time card identity on GameSim deltas (slower, may improve inferred move/buy/sell coverage)")
    parser.add_argument("--include-opponent-board", action="store_true",
                        help="Keep opponent board cards in deferred snapshots (more payload and DB work)")
    parser.add_argument("--enable-probes", action="store_true",
                        help="Attach passive probe hooks for method discovery (slower)")
    parser.add_argument("--broad-hooks", action="store_true",
                        help="Attach the older broad hook set for debugging (slower, more duplicate work)")
    parser.add_argument("--verbose-snapshots", action="store_true",
                        help="Print the legacy multi-line snapshot block instead of the default "
                             "compact one-line form (rate-limited to reduce console hitching)")
    parser.add_argument("--snapshot-print-interval-ms", type=float, default=None,
                        help="Minimum milliseconds between consecutive verbose snapshot blocks "
                             f"(default {int(_SNAPSHOT_PRINT_MIN_INTERVAL_MS)}ms; 0 disables throttling)")
    args = parser.parse_args()

    _do_log = args.log
    _do_db = args.db
    _VERBOSE_HOOKS = args.verbose_hooks
    _VERBOSE_DEBUG = args.verbose_debug
    _RENDER_ALL_SNAPSHOTS = args.all_snapshots
    _DETAILED_SNAPSHOTS = args.detailed_snapshots
    # Re-enabled from W/L-only scope-down: CLI args now respected
    _FULL_DELTA_CARDS = args.full_delta_cards or _FULL_DELTA_CARDS
    _DELTA_PLAYER_ATTRS = args.delta_player_attrs or _DELTA_PLAYER_ATTRS
    _ACTION_EVENT_CARDS = args.action_delta_cards or _ACTION_EVENT_CARDS
    _CAPTURE_OPPONENT_BOARD = args.include_opponent_board or _CAPTURE_OPPONENT_BOARD
    _ENABLE_PROBES = args.enable_probes  # kept False by default
    _ENABLE_BROAD_HOOKS = args.broad_hooks  # kept False by default
    _COMPACT_SNAPSHOTS = not args.verbose_snapshots
    if args.snapshot_print_interval_ms is not None:
        _SNAPSHOT_PRINT_MIN_INTERVAL_MS = max(0.0, args.snapshot_print_interval_ms)

    # Always start the background worker â€” it handles snapshot processing
    # (merge, dedup, action inference, rendering) in addition to persistence.
    # This keeps on_message thin and avoids back-pressuring Frida's send().
    start_db_writer()

    try:
        import frida
    except ImportError:
        print("[Mono] ERROR: frida not installed.")
        print("[Mono] Install with: pip install frida frida-tools")
        sys.exit(1)

    # Find game process
    pid = args.pid
    if pid is None:
        pid = find_game_pid(args.process)
        if pid is None:
            if args.wait:
                pid = wait_for_game_pid(args.process)
            else:
                print(f"[Mono] ERROR: {args.process} not found running.")
                print("[Mono] Start the game first, or use --wait.")
                sys.exit(1)

    print(f"[Mono] Attaching to PID {pid} ({args.process})...")

    # Setup output directory
    if _do_log:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        _output_dir = CAPTURES_DIR / f"mono_{ts}"
        _output_dir.mkdir(parents=True, exist_ok=True)
        _log_file = open(_output_dir / "capture.jsonl", "w")
        print(f"[Mono] Logging to {_output_dir}")

    # Attach and inject
    try:
        session = frida.attach(pid)
    except Exception as e:
        print(f"[Mono] Failed to attach: {e}")
        if "access" in str(e).lower():
            print("[Mono] Try running as Administrator.")
        sys.exit(1)

    script_source = FRIDA_MONO_AGENT.replace(
        "__FULL_DELTA_CARDS__",
        "true" if _FULL_DELTA_CARDS else "false",
    )
    script_source = script_source.replace(
        "__ENABLE_PROBES__",
        "true" if _ENABLE_PROBES else "false",
    )
    script_source = script_source.replace(
        "__ENABLE_BROAD_HOOKS__",
        "true" if _ENABLE_BROAD_HOOKS else "false",
    )
    script_source = script_source.replace(
        "__DELTA_PLAYER_ATTRS__",
        "true" if _DELTA_PLAYER_ATTRS else "false",
    )
    script_source = script_source.replace(
        "__ACTION_EVENT_CARDS__",
        "true" if _ACTION_EVENT_CARDS else "false",
    )
    script_source = script_source.replace(
        "__CAPTURE_OPPONENT_BOARD__",
        "true" if _CAPTURE_OPPONENT_BOARD else "false",
    )
    script_source = script_source.replace(
        "__VERBOSE_HOOK_CALLS__",
        "true" if _VERBOSE_HOOKS else "false",
    )
    script = session.create_script(script_source)
    script.on("message", on_message)
    script.load()

    print(f"\n{'=' * 60}")
    print(f"  MONO CAPTURE ACTIVE")
    print(f"  Play the game â€” game state snapshots will appear here")
    print(f"  Press Ctrl+C to stop")
    print(f"{'=' * 60}\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        print("\n[Mono] Detaching...")
        try:
            script.unload()
            session.detach()
        except Exception:
            pass
        stop_db_writer()
        if _log_file:
            _log_file.close()

        # Print probe summary if any
        if _probe_hits:
            print("\n[Mono] Probe hit summary:")
            for method, count in sorted(_probe_hits.items(), key=lambda x: -x[1]):
                print(f"  {method}: {count} calls")

        if _capture_calls:
            print("\n[Mono] Capture hook summary:")
            for method, count in sorted(_capture_calls.items(), key=lambda x: -x[1]):
                print(f"  {method}: {count} calls")

        if _duplicate_snapshot_count:
            print(f"[Mono] Duplicate snapshots skipped: {_duplicate_snapshot_count}")
        if _coalesced_snapshot_db_updates:
            print(f"[Mono] Coalesced snapshot DB updates: {_coalesced_snapshot_db_updates}")

        print(f"[Mono] Done. {_snapshot_count} snapshots captured.")
        if _output_dir:
            print(f"[Mono] Captures saved to: {_output_dir}")


if __name__ == "__main__":
    main()
