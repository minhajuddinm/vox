package com.minhaj.vox;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Reads the dictionary text (one entry per line, "wrong => right" for replacements). Same rules as
 * dictionary_terms and replacements in windows/vox_core.py; spec/golden.txt keeps the two in step.
 */
final class Terms {
    private Terms() { }

    /** Names and terms the speech and cleanup models should spell correctly, without duplicates. */
    static List<String> terms(String peopleRaw, String dictionaryRaw) {
        List<String> out = new ArrayList<>();
        for (String line : peopleRaw.split("\n")) {
            String l = line.trim();
            if (!l.isEmpty() && !l.startsWith("#")) add(out, l);
        }
        for (String line : dictionaryRaw.split("\n")) {
            String l = line.trim();
            if (l.isEmpty() || l.startsWith("#")) continue;
            if (l.contains("=>")) {
                String right = l.substring(l.indexOf("=>") + 2).trim();
                if (!right.isEmpty()) add(out, right);
            } else {
                add(out, l);
            }
        }
        return out;
    }

    /** Forced replacements from lines of the form "wrong => right". A later line for the same word wins. */
    static Map<String, String> replacements(String dictionaryRaw) {
        Map<String, String> out = new LinkedHashMap<>();
        for (String line : dictionaryRaw.split("\n")) {
            String l = line.trim();
            if (l.startsWith("#") || !l.contains("=>")) continue;
            String wrong = l.substring(0, l.indexOf("=>")).trim();
            String right = l.substring(l.indexOf("=>") + 2).trim();
            if (!wrong.isEmpty()) out.put(wrong, right);
        }
        return out;
    }

    /** Shortest term the fuzzy pass works on (and the shortest word it changes). */
    static final int FUZZY_MIN_LEN = 5;

    /** Shortest term that also fixes a spelling one letter off (shorter names sit next to real words: Alice, alike). */
    static final int FUZZY_NEAR_MIN_LEN = 7;

    /** Ordinary English words the fuzzy pass never touches (the same list as COMMON_WORDS in windows/vox_core.py). */
    private static final String COMMON =
        "about above after again agree alone along already always among another answer anyone anything around " +
        "asked asking based basic beach because become before began begin being below better between black " +
        "blank board bring broke brown build built bunch cause chain chair change charge check child choice " +
        "class clean clear click clock close cloud coffee color could count cover crash cross daily dance " +
        "dates delay doing doubt dozen draft drive early earth eight email empty enjoy enough entire equal " +
        "error event every exact extra faces fault field fifth final first fixed flash floor focus force " +
        "found frame fresh front fruit funny given glass going grace grand grant great green group guess " +
        "guide happy heard heart heavy hello house human ideas image issue items large later laugh layer " +
        "learn least leave level light likely limit local logic looks lower lunch maybe means might money " +
        "month mouse mouth movie music needs never night noise north noted notes novel number offer often " +
        "older order other paper party peace phone piece place plain plane plant point power press price " +
        "pride print prior prize proof proud quick quiet quite radio raise range rapid reach ready right " +
        "rough round route royal salad sales scale scene score sense serve seven shall shape share sharp " +
        "sheet shift short shown sight simple since sleep slice slide small smart smile solid solve sorry " +
        "sound south space speak speed spend split spoke sport stack staff stage stand start state still " +
        "stock stone stood store storm story study stuff style sugar super sweet table taken taste teach " +
        "thank their theme there these thing think third those three threw throw tight times title today " +
        "token total touch tough tower track trade train treat trend trial tried truck truly trust truth " +
        "twice under union until upper urban usage usual value video visit voice waste watch water wheel " +
        "where which while white whole whose woman women world worry worse worth would write wrong yield " +
        "young yours " +
        "acute adapt admit adopt adult agent alarm album alert alive allow alter ample angle angry apart " +
        "apply argue arise aware awful basis begun bible blame blind block blood bonus boost brain " +
        "brand bread break brief broad brush buyer cable carry catch cease chart chase cheap chief civil " +
        "claim clause clauses climb coach curve cycle dealt decker depth dirty docket drama dream dress drink " +
        "drove eager enter essay exist fancy fiber fight flame flank fleet flesh fluid frank fraud fully " +
        "giant glory goggle guard guest guilty habit handy harsh hence hotel humor ideal imply index " +
        "inner input intro jelly joint judge knife known label lemon linux loose lotion lucky magic " +
        "major march match mayor media metal minor minus mixed model motel motion nation noble nurse " +
        "occur ocean opera outer owner panic pause phase photo pilot pitch pixel plate plenty polite potion " +
        "pound prime queen quest quote rally reply rider rival robot rocker rocky rural scope serum shack " +
        "shade shark shelf shell shine shirt shock shoot skill sleek slick slicker slope smoke snack snake " +
        "solar spare spark spell spice spine spite sprint steam steel steep stern stick stiff stove strap " +
        "straw stride strike strip strive stroke strong swing sword teeth tenth thick thumb " +
        "tiger tired toast toggle topic torch trace tribe trick trunk tutor twist ultra uncle unite unity " +
        "upset vague valid vital vowel wagon weird whale wheat wider wound wrist youth ";
    private static final Set<String> COMMON_WORDS = new HashSet<>(Arrays.asList(COMMON.trim().split(" ")));

    /** True for a lowercase word on the COMMON list (AutoLearn's ordinary words use it too). */
    static boolean isCommonWord(String lower) {
        return COMMON_WORDS.contains(lower);
    }
    private static final Pattern WORD = Pattern.compile("[\\p{L}\\p{N}_]+");

    /**
     * Puts the dictionary's spelling on words that are the same word in another case or one letter off. Same rules as
     * fuzzy_dictionary in windows/vox_core.py: only terms that are one word of five letters or more take part; a word of
     * that length is changed when it equals a term ignoring case, or, for a term of seven letters or more only, is one
     * edit from exactly one such term with the same first letter; never an ordinary English word or one with a digit or
     * underscore. spec/golden.txt (fuzzydict) keeps the two in step.
     */
    static String fuzzy(String text, List<String> terms) {
        Map<String, String> byLower = new LinkedHashMap<>();
        for (String t : terms) {
            t = t.trim();
            if (t.length() >= FUZZY_MIN_LEN && isAlpha(t)) byLower.putIfAbsent(t.toLowerCase(Locale.ROOT), t);
        }
        if (byLower.isEmpty() || text.isEmpty()) return text;
        Matcher m = WORD.matcher(text);
        StringBuffer sb = new StringBuffer();
        while (m.find()) m.appendReplacement(sb, Matcher.quoteReplacement(fixWord(m.group(), byLower)));
        return m.appendTail(sb).toString();
    }

    private static String fixWord(String w, Map<String, String> byLower) {
        String lw = w.toLowerCase(Locale.ROOT);
        if (w.length() < FUZZY_MIN_LEN || !isAlpha(w) || COMMON_WORDS.contains(lw)) return w;
        String exact = byLower.get(lw);
        if (exact != null) return exact;
        Set<String> near = new HashSet<>();
        for (Map.Entry<String, String> e : byLower.entrySet()) {
            String k = e.getKey();
            if (k.length() >= FUZZY_NEAR_MIN_LEN && k.charAt(0) == lw.charAt(0) && oneEdit(lw, k)) near.add(e.getValue());
        }
        return near.size() == 1 ? near.iterator().next() : w;
    }

    private static boolean isAlpha(String s) {
        for (int i = 0; i < s.length(); i++) if (!Character.isLetter(s.charAt(i))) return false;
        return !s.isEmpty();
    }

    /** True when the different strings a and b are one substitution, insertion or deletion apart (not a letter added at the end). */
    private static boolean oneEdit(String a, String b) {
        if (Math.abs(a.length() - b.length()) > 1) return false;
        int i = 0, n = Math.min(a.length(), b.length());
        while (i < n && a.charAt(i) == b.charAt(i)) i++;
        if (a.length() == b.length()) return a.substring(i + 1).equals(b.substring(i + 1));
        String longer = a.length() > b.length() ? a : b, shorter = longer == a ? b : a;
        return i != shorter.length() && longer.substring(i + 1).equals(shorter.substring(i));
    }

    private static void add(List<String> out, String term) {
        if (!out.contains(term)) out.add(term);
    }
}
