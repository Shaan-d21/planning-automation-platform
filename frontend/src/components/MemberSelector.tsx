import { useEffect, useMemo, useState, type KeyboardEvent } from "react";

import { api } from "../api/client";
import type { DataReviewMember } from "../api/types";
import { Icon } from "./Icon";

interface MemberSelectorProps {
  cube: string;
  dimension: string;
  members: string[];
  multiple: boolean;
  ariaLabel: string;
  onChange: (members: string[]) => void;
}

export function MemberSelector({ cube, dimension, members, multiple, ariaLabel, onChange }: MemberSelectorProps) {
  const selectedValue = members[0] ?? "";
  const [draft, setDraft] = useState(multiple ? "" : selectedValue);
  const [suggestions, setSuggestions] = useState<DataReviewMember[]>([]);
  const [totalMatches, setTotalMatches] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadedCount, setLoadedCount] = useState(0);
  const [searchUnavailable, setSearchUnavailable] = useState<string | null>(null);
  const [activeIndex, setActiveIndex] = useState(-1);

  useEffect(() => {
    setOpen(false);
    setSuggestions([]);
    setLoadedCount(0);
    setSearchUnavailable(null);
    setDraft(multiple ? "" : selectedValue);
  }, [cube, dimension, multiple]);

  useEffect(() => {
    if (!multiple && !open && selectedValue !== draft) setDraft(selectedValue);
  }, [draft, multiple, open, selectedValue]);

  useEffect(() => {
    if (!open || !cube.trim() || !dimension.trim()) return;
    let active = true;
    const timer = window.setTimeout(() => {
      setLoading(true);
      api.dataReviewMembers(cube, dimension, draft.trim(), 0, 40)
        .then((response) => {
          if (!active) return;
          setSuggestions(response.members);
          setLoadedCount((response.offset ?? 0) + response.members.length);
          setTotalMatches(response.total_matches);
          setHasMore(response.has_more);
          setSearchUnavailable(null);
          setActiveIndex(response.members.length ? 0 : -1);
        })
        .catch((reason: unknown) => {
          if (!active) return;
          setSuggestions([]);
          setTotalMatches(0);
          setHasMore(false);
          setActiveIndex(-1);
          setSearchUnavailable(errorMessage(reason));
        })
        .finally(() => active && setLoading(false));
    }, 250);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [cube, dimension, draft, open]);

  const availableSuggestions = useMemo(() => {
    const selected = new Set(members.map((member) => member.toLowerCase()));
    return multiple
      ? suggestions.filter((member) => !selected.has(member.name.toLowerCase()))
      : suggestions;
  }, [members, multiple, suggestions]);

  function selectMember(member: DataReviewMember) {
    if (multiple) {
      addMembers([member.name]);
      setDraft("");
      setOpen(true);
    } else {
      onChange([member.name]);
      setDraft(member.name);
      setOpen(false);
    }
  }

  async function loadMore() {
    if (loadingMore || !hasMore) return;
    setLoadingMore(true);
    try {
      const response = await api.dataReviewMembers(
        cube,
        dimension,
        draft.trim(),
        loadedCount,
        100
      );
      setSuggestions((current) => {
        const unique = new Map(
          current.map((member) => [memberKey(member), member])
        );
        response.members.forEach((member) => unique.set(memberKey(member), member));
        return [...unique.values()];
      });
      setLoadedCount((response.offset ?? loadedCount) + response.members.length);
      setTotalMatches(response.total_matches);
      setHasMore(response.has_more);
      setSearchUnavailable(null);
    } catch (reason) {
      setSearchUnavailable(errorMessage(reason));
    } finally {
      setLoadingMore(false);
    }
  }

  function selectVisible() {
    addMembers(availableSuggestions.map((member) => member.name));
  }

  function addMembers(additions: string[]) {
    const unique = new Map(members.map((member) => [member.toLowerCase(), member]));
    additions.forEach((member) => {
      const normalized = member.trim();
      if (normalized) unique.set(normalized.toLowerCase(), normalized);
    });
    onChange([...unique.values()]);
  }

  function commitExact() {
    const additions = splitMembers(draft);
    if (!additions.length) return;
    if (multiple) {
      addMembers(additions);
      setDraft("");
    } else {
      onChange([additions[0]]);
      setDraft(additions[0]);
    }
  }

  function changeDraft(value: string) {
    setDraft(value);
    setOpen(true);
    if (!multiple) onChange(value.trim() ? [value] : []);
  }

  function keyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown" && availableSuggestions.length) {
      event.preventDefault();
      setActiveIndex((current) => Math.min(current + 1, availableSuggestions.length - 1));
      return;
    }
    if (event.key === "ArrowUp" && availableSuggestions.length) {
      event.preventDefault();
      setActiveIndex((current) => Math.max(current - 1, 0));
      return;
    }
    if (event.key === "Escape") {
      setOpen(false);
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const selected = availableSuggestions[activeIndex];
      if (open && selected) selectMember(selected);
      else commitExact();
      return;
    }
    if (multiple && [",", ";", "|"].includes(event.key)) {
      event.preventDefault();
      commitExact();
      return;
    }
    if (multiple && event.key === "Backspace" && !draft && members.length) {
      onChange(members.slice(0, -1));
    }
  }

  return <div className="member-picker-field">
    <span>{multiple ? "Members" : "Member"}</span>
    <div className="member-picker">
      <div className={`member-token-box${open ? " is-open" : ""}`}>
        {multiple && members.map((member) => <span className="member-token" key={member}>{member}<button type="button" aria-label={`Remove ${member}`} onClick={() => onChange(members.filter((value) => value !== member))}><Icon name="close" /></button></span>)}
        <input
          role="combobox"
          aria-label={ariaLabel}
          aria-autocomplete="list"
          aria-expanded={open}
          aria-controls={`member-options-${safeId(ariaLabel)}`}
          value={draft}
          onChange={(event) => changeDraft(event.target.value)}
          onFocus={() => setOpen(true)}
          onKeyDown={keyDown}
          onBlur={() => { commitExact(); window.setTimeout(() => setOpen(false), 120); }}
          placeholder={!dimension ? "Choose a dimension first" : multiple && members.length ? "Search or add another" : "Search Oracle members"}
          disabled={!dimension}
        />
        {loading && <span className="member-picker__spinner" aria-label="Searching members"><span className="spinner" /></span>}
        <button type="button" className="member-picker__browse" disabled={!dimension} onMouseDown={(event) => event.preventDefault()} onClick={() => { if (!open) setDraft(""); setOpen((current) => !current); }}><Icon name="tasks" /> Browse</button>
      </div>
      {open && dimension && <div className="member-options" id={`member-options-${safeId(ariaLabel)}`} role="listbox">
        <header><span><strong>Live Oracle members</strong><small>{loading ? "Loading hierarchy..." : totalMatches ? `${totalMatches.toLocaleString()} available` : "No members returned"}</small></span>{multiple && availableSuggestions.length > 0 && <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={selectVisible}>Select loaded</button>}</header>
        {searchUnavailable ? <div className="member-options__message is-warning"><Icon name="alert" /><span><strong>Member search unavailable</strong><small>{searchUnavailable} You may still enter an exact member name.</small></span></div> : availableSuggestions.length ? <div className="member-options__list">{availableSuggestions.map((member, index) => <button
          type="button"
          role="option"
          aria-selected={index === activeIndex}
          className={index === activeIndex ? "is-active" : ""}
          onMouseDown={(event) => event.preventDefault()}
          onMouseEnter={() => setActiveIndex(index)}
          onClick={() => selectMember(member)}
          key={`${member.path ?? member.name}-${member.name}`}
        ><span><strong>{member.name}</strong>{member.alias && member.alias !== member.name && <small>{member.alias}</small>}</span><small>{memberPath(member)}</small></button>)}</div> : !loading && <div className="member-options__message"><Icon name="search" /><span><strong>No matching Oracle members</strong><small>Check the spelling or press Enter to use an exact member expression.</small></span></div>}
        {hasMore && !searchUnavailable && <footer><span>{loadedCount.toLocaleString()} of {totalMatches.toLocaleString()} loaded</span><button type="button" disabled={loadingMore} onMouseDown={(event) => event.preventDefault()} onClick={() => void loadMore()}>{loadingMore ? <><span className="spinner" /> Loading...</> : "Load more"}</button></footer>}
      </div>}
    </div>
  </div>;
}

function memberPath(member: DataReviewMember) {
  if (member.path) return member.path;
  if (member.parent_name) return `Under ${member.parent_name}`;
  return member.has_children ? "Parent member" : "Planning member";
}

function memberKey(member: DataReviewMember) {
  return `${member.path ?? ""}|${member.name}`.toLowerCase();
}

function splitMembers(value: string) {
  return value.split(/[|,;\n]+/).map((member) => member.trim()).filter(Boolean);
}

function safeId(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
}

function errorMessage(reason: unknown) {
  return reason instanceof Error ? reason.message : "Oracle member search could not be completed.";
}
