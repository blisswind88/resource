<?xml version='1.0' encoding='UTF-8'?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron" queryBinding="xslt">
  <!-- 생성 파일 — rules.yaml에서 build_schematron.py가 만들었습니다. 직접 고치지 마십시오. -->
  <sch:title>T19 규칙 목록 (13번 DITA 규칙 검증)</sch:title>
  <sch:pattern id="pat-R13-001">
    <sch:title>R13-001 맵 navtitle = 토픽 title</sch:title>
    <sch:rule context="*[@href][topicmeta/navtitle][not(@scope = 'external')][not(@format) or @format = 'dita'][not(contains(@href, '.ditamap'))]">
      <sch:let name="file" value="substring-before(concat(@href, '#'), '#')"/>
      <sch:let name="topic" value="document($file, .)/*"/>
      <sch:let name="nav" value="normalize-space(string(topicmeta/navtitle))"/>
      <sch:let name="ttl" value="normalize-space(string($topic/title[1]))"/>
      <sch:assert test="not($topic) or @locktitle = 'yes' or $nav = $ttl" id="R13-001" role="error" diagnostics="R13-001.expected R13-001.actual">맵 navtitle과 토픽 title이 다릅니다 (<sch:value-of select="@href"/>)</sch:assert>
      <sch:report test="$topic and @locktitle = 'yes' and $nav != $ttl" id="R13-001.locked" role="info" diagnostics="R13-001.locked.expected R13-001.locked.actual">의도적 차이 (locktitle="yes") — 목차에는 맵 라벨, 본문 제목에는 토픽 title이 나옵니다 (<sch:value-of select="@href"/>)</sch:report>
      <sch:report test="not($topic)" id="R13-001.missing" role="info">토픽 파일을 읽을 수 없어 비교하지 않았습니다 — 14번 링크 검사 대상 (<sch:value-of select="@href"/>)</sch:report>
    </sch:rule>
  </sch:pattern>
  <sch:diagnostics>
    <sch:diagnostic id="R13-001.expected">
      <sch:value-of select="$nav"/>
    </sch:diagnostic>
    <sch:diagnostic id="R13-001.actual">
      <sch:value-of select="$ttl"/>
    </sch:diagnostic>
    <sch:diagnostic id="R13-001.locked.expected">
      <sch:value-of select="$nav"/>
    </sch:diagnostic>
    <sch:diagnostic id="R13-001.locked.actual">
      <sch:value-of select="$ttl"/>
    </sch:diagnostic>
  </sch:diagnostics>
</sch:schema>
